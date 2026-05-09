"""Failing tests for PushPipeline.handle_change (P1-18) and push extras (P1-20, P1-21)."""

from hashlib import sha256
from pathlib import Path
from unittest.mock import MagicMock, Mock, PropertyMock, patch

import pytest

from corpus_forge.sync.push import PushPipeline

# ── helpers ──────────────────────────────────────────────────────────────


def _make_pipeline(
    backend=None,
    dataset_id: int = 1,
    echo_suppressor=None,
    host_id: str = "test-host",
) -> PushPipeline:
    backend = backend or MagicMock()
    echo_suppressor = echo_suppressor or MagicMock()
    return PushPipeline(
        backend=backend,
        dataset_id=dataset_id,
        echo_suppressor=echo_suppressor,
        host_id=host_id,
    )


@pytest.fixture
def mock_path():
    path = MagicMock(spec=Path)
    path.resolve.return_value = Path("/vault/doc.md")
    path.read_text.return_value = "hello world"
    return path


@pytest.fixture
def mock_lock():
    lock = MagicMock()
    lock.__enter__ = MagicMock(return_value=None)
    lock.__exit__ = MagicMock(return_value=None)
    return lock


# ── P1-18 Step 1: mtime pre-filter ──────────────────────────────────────


class TestMtimePreFilter:
    """Step 1 — skip when mtime is unchanged."""

    def test_skips_when_mtime_matches_cache(self, mock_path):
        """Cached mtime matches current stat().st_mtime → return early."""
        pipeline = _make_pipeline()
        pipeline._mtime_cache["/vault/doc.md"] = 1000.0
        type(mock_path.stat.return_value).st_mtime = PropertyMock(return_value=1000.0)

        pipeline.handle_change(mock_path)

        pipeline._backend.lock_source.assert_not_called()
        pipeline._backend.resolve_document.assert_not_called()

    def test_proceeds_when_mtime_differs(self, mock_path):
        """Cached mtime differs from current → proceed."""
        pipeline = _make_pipeline()
        pipeline._mtime_cache["/vault/doc.md"] = 500.0
        type(mock_path.stat.return_value).st_mtime = PropertyMock(return_value=1000.0)

        pipeline.handle_change(mock_path)

        mock_path.read_text.assert_called_once()

    def test_proceeds_on_first_visit(self, mock_path):
        """No cached mtime → proceed (first time seeing this path)."""
        pipeline = _make_pipeline()
        type(mock_path.stat.return_value).st_mtime = PropertyMock(return_value=1000.0)

        pipeline.handle_change(mock_path)

        mock_path.read_text.assert_called_once()


# ── P1-18 Step 2: EchoSuppressor check ──────────────────────────────────


class TestEchoSuppressorMatch:
    """Step 2 — skip when EchoSuppressor matches."""

    def test_skips_when_echo_suppressor_matches(self, mock_path):
        """was_just_written(path, hash) returns True → skip, no lock_source."""
        pipeline = _make_pipeline()
        pipeline._echo_suppressor.was_just_written.return_value = True
        type(mock_path.stat.return_value).st_mtime = PropertyMock(return_value=1000.0)

        pipeline.handle_change(mock_path)

        pipeline._backend.lock_source.assert_not_called()

    def test_proceeds_when_echo_suppressor_does_not_match(self, mock_path):
        """was_just_written returns False → acquire lock_source."""
        pipeline = _make_pipeline()
        pipeline._echo_suppressor.was_just_written.return_value = False
        type(mock_path.stat.return_value).st_mtime = PropertyMock(return_value=1000.0)

        pipeline.handle_change(mock_path)

        pipeline._backend.lock_source.assert_called_once()


# ── P1-18 Step 3: content_hash unchanged ────────────────────────────────


class TestContentHashUnchanged:
    """Step 3a — local content_hash matches latest revision → no-op."""

    def test_noop_when_hash_matches_latest_revision(self, mock_path, mock_lock):
        """local_hash == latest.content_hash → no insert_revision / upsert_document."""
        pipeline = _make_pipeline()
        pipeline._echo_suppressor.was_just_written.return_value = False
        type(mock_path.stat.return_value).st_mtime = PropertyMock(return_value=1000.0)
        pipeline._backend.lock_source.return_value = mock_lock
        pipeline._backend.resolve_document.return_value = {"id": 42}
        pipeline._backend.latest_revision.return_value = {
            "id": 5,
            "revision_number": 3,
            "content_hash": "same_hash",
        }

        with _patch_chunk_hash("same_hash"):
            pipeline.handle_change(mock_path)

        pipeline._backend.insert_revision.assert_not_called()
        pipeline._backend.upsert_document.assert_not_called()

    def test_noop_when_no_revisions_and_doc_hash_matches(self, mock_path, mock_lock):
        """No revisions yet, but doc.content_hash matches local → no-op."""
        pipeline = _make_pipeline()
        pipeline._echo_suppressor.was_just_written.return_value = False
        type(mock_path.stat.return_value).st_mtime = PropertyMock(return_value=1000.0)
        pipeline._backend.lock_source.return_value = mock_lock
        pipeline._backend.resolve_document.return_value = {
            "id": 42,
            "content_hash": "same_hash",
        }
        pipeline._backend.latest_revision.return_value = None

        with _patch_chunk_hash("same_hash"):
            pipeline.handle_change(mock_path)

        pipeline._backend.insert_revision.assert_not_called()
        pipeline._backend.upsert_document.assert_not_called()


# ── P1-18 Step 3: content_hash changed ──────────────────────────────────


class TestContentHashChanged:
    """Step 3b — local content_hash differs from latest → insert_revision + upsert_document."""

    def test_inserts_revision_and_upserts_document(self, mock_path, mock_lock):
        """Different hash → insert_revision and upsert_document called."""
        pipeline = _make_pipeline()
        pipeline._echo_suppressor.was_just_written.return_value = False
        type(mock_path.stat.return_value).st_mtime = PropertyMock(return_value=1000.0)
        pipeline._backend.lock_source.return_value = mock_lock
        pipeline._backend.resolve_document.return_value = {"id": 42}
        pipeline._backend.latest_revision.return_value = {
            "id": 5,
            "revision_number": 3,
            "content_hash": "old_hash",
        }
        pipeline._backend.insert_revision.return_value = {"id": 6, "revision_number": 4}

        with _patch_chunk_hash("new_hash"):
            pipeline.handle_change(mock_path)

        pipeline._backend.insert_revision.assert_called_once()
        pipeline._backend.upsert_document.assert_called_once()

    def test_insert_revision_receives_correct_params(self, mock_path, mock_lock):
        """insert_revision called with parent=latest.id, content_hash, text, author_host=host_id."""
        pipeline = _make_pipeline(dataset_id=7, host_id="macA")
        pipeline._echo_suppressor.was_just_written.return_value = False
        type(mock_path.stat.return_value).st_mtime = PropertyMock(return_value=1000.0)
        pipeline._backend.lock_source.return_value = mock_lock
        pipeline._backend.resolve_document.return_value = {"id": 42}
        pipeline._backend.latest_revision.return_value = {
            "id": 5,
            "revision_number": 3,
            "content_hash": "old_hash",
        }
        pipeline._backend.insert_revision.return_value = {"id": 6, "revision_number": 4}

        with _patch_chunk_hash("new_hash"):
            pipeline.handle_change(mock_path)

        pipeline._backend.insert_revision.assert_called_once()
        call_kwargs = pipeline._backend.insert_revision.call_args[1]
        assert call_kwargs["document_id"] == 42
        assert call_kwargs["content_hash"] == "new_hash"
        assert call_kwargs["text"] == "hello world"
        assert call_kwargs["parent_revision_id"] == 5
        assert call_kwargs["author_host"] == "macA"

    def test_upsert_document_called_inside_lock(self, mock_path, mock_lock):
        """upsert_document call happens within the lock_source context."""
        pipeline = _make_pipeline()
        pipeline._echo_suppressor.was_just_written.return_value = False
        type(mock_path.stat.return_value).st_mtime = PropertyMock(return_value=1000.0)
        pipeline._backend.lock_source.return_value = mock_lock
        pipeline._backend.resolve_document.return_value = {"id": 42}
        pipeline._backend.latest_revision.return_value = {
            "id": 5,
            "content_hash": "old_hash",
        }
        pipeline._backend.insert_revision.return_value = {"id": 6, "revision_number": 4}

        with _patch_chunk_hash("new_hash"):
            pipeline.handle_change(mock_path)

        mock_lock.__enter__.assert_called_once()
        mock_lock.__exit__.assert_called_once()

    def test_new_document_with_no_prior_revision(self, mock_path, mock_lock):
        """Document exists but has no revisions → first revision insert works."""
        pipeline = _make_pipeline(host_id="macA")
        pipeline._echo_suppressor.was_just_written.return_value = False
        type(mock_path.stat.return_value).st_mtime = PropertyMock(return_value=1000.0)
        pipeline._backend.lock_source.return_value = mock_lock
        pipeline._backend.resolve_document.return_value = {"id": 42}
        pipeline._backend.latest_revision.return_value = None
        pipeline._backend.insert_revision.return_value = {"id": 1, "revision_number": 1}

        with _patch_chunk_hash("first_hash"):
            pipeline.handle_change(mock_path)

        pipeline._backend.insert_revision.assert_called_once_with(
            document_id=42,
            content_hash="first_hash",
            text="hello world",
            parent_revision_id=None,
            author_host="macA",
            is_tombstone=False,
        )
        pipeline._backend.upsert_document.assert_called_once()


# ── P1-18: lock_source context management ───────────────────────────────


class TestLockSourceContext:
    """lock_source context manager is held through the revision decision scope."""

    def test_lock_source_entered_and_exited(self, mock_path, mock_lock):
        """lock_source.__enter__ and __exit__ are called once each."""
        pipeline = _make_pipeline()
        pipeline._echo_suppressor.was_just_written.return_value = False
        type(mock_path.stat.return_value).st_mtime = PropertyMock(return_value=1000.0)
        pipeline._backend.lock_source.return_value = mock_lock
        pipeline._backend.resolve_document.return_value = {"id": 42}
        pipeline._backend.latest_revision.return_value = {
            "id": 5,
            "content_hash": "old_hash",
        }
        pipeline._backend.insert_revision.return_value = {"id": 6, "revision_number": 4}

        with _patch_chunk_hash("new_hash"):
            pipeline.handle_change(mock_path)

        pipeline._backend.lock_source.assert_called_once()
        mock_lock.__enter__.assert_called_once()
        mock_lock.__exit__.assert_called_once()

    def test_lock_source_released_on_exception(self, mock_path, mock_lock):
        """When insert_revision raises, lock_source is still released."""
        pipeline = _make_pipeline()
        pipeline._echo_suppressor.was_just_written.return_value = False
        type(mock_path.stat.return_value).st_mtime = PropertyMock(return_value=1000.0)
        pipeline._backend.lock_source.return_value = mock_lock
        pipeline._backend.resolve_document.return_value = {"id": 42}
        pipeline._backend.latest_revision.return_value = {
            "id": 5,
            "content_hash": "old_hash",
        }
        pipeline._backend.insert_revision.side_effect = RuntimeError("db error")

        with _patch_chunk_hash("new_hash"), pytest.raises(RuntimeError):
            pipeline.handle_change(mock_path)

        mock_lock.__exit__.assert_called_once()

    def test_lock_source_not_acquired_when_echo_suppressor_matches(self, mock_path):
        """No lock acquisition when echo suppressor short-circuits."""
        pipeline = _make_pipeline()
        pipeline._echo_suppressor.was_just_written.return_value = True
        type(mock_path.stat.return_value).st_mtime = PropertyMock(return_value=1000.0)

        pipeline.handle_change(mock_path)

        pipeline._backend.lock_source.assert_not_called()

    def test_lock_source_not_acquired_when_mtime_unchanged(self, mock_path):
        """No lock acquisition when mtime pre-filter short-circuits."""
        pipeline = _make_pipeline()
        pipeline._mtime_cache["/vault/doc.md"] = 1000.0
        type(mock_path.stat.return_value).st_mtime = PropertyMock(return_value=1000.0)

        pipeline.handle_change(mock_path)

        pipeline._backend.lock_source.assert_not_called()


# ── P1-19 Step 1: start() creates watchdog Observer ──────────────────────


class TestStartObserver:
    """Step 1 — start() spins up a watchdog.observers.Observer."""

    def test_start_creates_observer_and_schedules_handler(self):
        """start() constructs a watchdog.observers.Observer, schedules a handler, starts."""
        pipeline = _make_pipeline()
        src = Path("/tmp/vault")

        with patch("watchdog.observers.Observer") as mock_obs_cls:
            mock_obs = mock_obs_cls.return_value
            pipeline.start(src, exclude_globs=("*.icloud",))

        mock_obs_cls.assert_called_once()
        mock_obs.schedule.assert_called_once()
        assert str(mock_obs.schedule.call_args[0][1]) == str(src)
        mock_obs.start.assert_called_once()

    def test_start_stores_exclude_globs(self):
        """exclude_globs stored for _should_ignore."""
        pipeline = _make_pipeline()

        with patch("watchdog.observers.Observer"):
            pipeline.start(Path("/vault"), exclude_globs=("*.icloud", "*.tmp"))

        assert pipeline._exclude_globs == ("*.icloud", "*.tmp")

    def test_start_stores_debounce_seconds(self):
        """debounce_seconds stored on pipeline."""
        pipeline = _make_pipeline()

        with patch("watchdog.observers.Observer"):
            pipeline.start(Path("/vault"), debounce_seconds=2.5)

        assert pipeline._debounce_seconds == 2.5

    def test_start_default_debounce_seconds(self):
        """Default debounce_seconds is 1.0."""
        pipeline = _make_pipeline()

        with patch("watchdog.observers.Observer"):
            pipeline.start(Path("/vault"))

        assert pipeline._debounce_seconds == 1.0


# ── P1-19 Step 2: _should_ignore filtering ────────────────────────────────


class TestShouldIgnore:
    """Step 2 — _should_ignore returns True for ignorable paths."""

    def test_ignores_directories(self):
        pipeline = _make_pipeline()
        pipeline._exclude_globs = ()
        path = MagicMock(spec=Path)
        path.is_dir.return_value = True
        assert pipeline._should_ignore(path) is True

    def test_ignores_hidden_files(self):
        pipeline = _make_pipeline()
        pipeline._exclude_globs = ()
        path = MagicMock(spec=Path)
        path.is_dir.return_value = False
        type(path).name = PropertyMock(return_value=".hidden.md")
        assert pipeline._should_ignore(path) is True

    def test_ignores_icloud_placeholders(self):
        pipeline = _make_pipeline()
        pipeline._exclude_globs = ()
        path = MagicMock(spec=Path)
        path.is_dir.return_value = False
        type(path).name = PropertyMock(return_value="doc.md.icloud")
        type(path).suffix = PropertyMock(return_value=".icloud")
        assert pipeline._should_ignore(path) is True

    def test_ignores_exclude_globs_match(self):
        pipeline = _make_pipeline()
        pipeline._exclude_globs = ("*.tmp", "*.log")
        path = MagicMock(spec=Path)
        path.is_dir.return_value = False
        type(path).name = PropertyMock(return_value="output.tmp")
        assert pipeline._should_ignore(path) is True

    def test_ignores_dataless_entries(self):
        pipeline = _make_pipeline()
        pipeline._exclude_globs = ()
        path = MagicMock(spec=Path)
        path.is_dir.return_value = False
        type(path).name = PropertyMock(return_value="online.md")
        stat_mock = MagicMock()
        stat_mock.st_blocks = 0
        path.stat.return_value = stat_mock
        assert pipeline._should_ignore(path) is True

    def test_allows_normal_file(self):
        pipeline = _make_pipeline()
        pipeline._exclude_globs = ()
        path = MagicMock(spec=Path)
        path.is_dir.return_value = False
        type(path).name = PropertyMock(return_value="readme.md")
        stat_mock = MagicMock()
        stat_mock.st_blocks = 8
        path.stat.return_value = stat_mock
        assert pipeline._should_ignore(path) is False

    def test_empty_exclude_globs_allows_normal_file(self):
        pipeline = _make_pipeline()
        pipeline._exclude_globs = ()
        path = MagicMock(spec=Path)
        path.is_dir.return_value = False
        type(path).name = PropertyMock(return_value="readme.md")
        stat_mock = MagicMock()
        stat_mock.st_blocks = 8
        path.stat.return_value = stat_mock
        assert pipeline._should_ignore(path) is False


# ── P1-19 Step 3: stop() joins observer ────────────────────────────────


class TestStopObserver:
    """Step 3 — stop() stops and joins the observer."""

    def test_stop_stops_and_joins_observer(self):
        pipeline = _make_pipeline()
        with patch("watchdog.observers.Observer") as mock_obs_cls:
            mock_obs = mock_obs_cls.return_value
            pipeline.start(Path("/vault"))
            pipeline.stop()
        mock_obs.stop.assert_called_once()
        mock_obs.join.assert_called_once()

    def test_stop_without_start_raises(self):
        pipeline = _make_pipeline()
        with pytest.raises(RuntimeError, match="not started"):
            pipeline.stop()


# ── P1-19 Step 4: debounce per-path ──────────────────────────────────────


class TestDebounce:
    """Step 4 — debounce coalesces rapid events per-path."""

    def test_same_path_coalesces_rapid_events(self):
        pipeline = _make_pipeline()
        timers: list[Mock] = []

        def _factory(delay, func, args=(), kwargs=None):
            t = MagicMock()
            t.start = MagicMock()
            timers.append(t)
            return t

        with (
            patch("watchdog.observers.Observer") as mock_obs_cls,
            patch("threading.Timer", side_effect=_factory),
        ):
            mock_obs = mock_obs_cls.return_value
            pipeline.start(Path("/vault"), debounce_seconds=1.0)
            handler = mock_obs.schedule.call_args[0][0]
            ev = MagicMock(src_path="/vault/doc.md", is_directory=False)
            handler.on_modified(ev)
            handler.on_modified(ev)

        assert len(timers) == 2
        timers[0].cancel.assert_called_once()
        timers[1].cancel.assert_not_called()

    def test_different_paths_independent(self):
        pipeline = _make_pipeline()
        timers_by_path: dict[str, list[Mock]] = {}

        def _factory(delay, func, args=(), kwargs=None):
            t = MagicMock()
            t.start = MagicMock()
            path_key = str(args[0]) if args else "?"
            timers_by_path.setdefault(path_key, []).append(t)
            return t

        with (
            patch("watchdog.observers.Observer") as mock_obs_cls,
            patch("threading.Timer", side_effect=_factory),
        ):
            mock_obs = mock_obs_cls.return_value
            pipeline.start(Path("/vault"), debounce_seconds=1.0)
            handler = mock_obs.schedule.call_args[0][0]
            ev_a = MagicMock(src_path="/vault/a.md", is_directory=False)
            ev_b = MagicMock(src_path="/vault/b.md", is_directory=False)
            handler.on_modified(ev_a)
            handler.on_modified(ev_b)

        assert len(timers_by_path.get("/vault/a.md", [])) == 1
        assert len(timers_by_path.get("/vault/b.md", [])) == 1
        timers_by_path["/vault/a.md"][0].cancel.assert_not_called()
        timers_by_path["/vault/b.md"][0].cancel.assert_not_called()

    def test_timer_uses_debounce_seconds(self):
        pipeline = _make_pipeline()
        with (
            patch("watchdog.observers.Observer") as mock_obs_cls,
            patch("threading.Timer") as mock_timer_cls,
        ):
            mock_obs = mock_obs_cls.return_value
            pipeline.start(Path("/vault"), debounce_seconds=3.0)
            handler = mock_obs.schedule.call_args[0][0]
            ev = MagicMock(src_path="/vault/doc.md", is_directory=False)
            handler.on_modified(ev)
        assert mock_timer_cls.call_args[0][0] == 3.0


# ── P1-20: Cloud-duplicate cleanup (_handle_cloud_duplicate) ─────────────


class TestCloudDuplicateSameHash:
    """_handle_cloud_duplicate — same hash → unlink, no revision."""

    def test_same_hash_unlinks_path(self):
        """Hash matches canonical → path.unlink() called."""
        pipeline = _make_pipeline(host_id="macA")
        path = MagicMock(spec=Path)
        path.read_text.return_value = "same content"
        canonical_path = MagicMock(spec=Path)
        canonical_path.exists.return_value = True
        canonical_path.read_text.return_value = "same content"

        with patch("corpus_forge.sync.push.is_cloud_duplicate") as mock_icd:
            mock_icd.return_value = (True, "icloud", canonical_path)
            with _patch_chunk_hash("abc123"):
                pipeline._handle_cloud_duplicate(path)

        path.unlink.assert_called_once()

    def test_same_hash_skips_revision_ops(self):
        """Hash matches → no insert_revision or upsert_document."""
        pipeline = _make_pipeline(host_id="macA")
        path = MagicMock(spec=Path)
        path.read_text.return_value = "same content"
        canonical_path = MagicMock(spec=Path)
        canonical_path.exists.return_value = True
        canonical_path.read_text.return_value = "same content"

        with patch("corpus_forge.sync.push.is_cloud_duplicate") as mock_icd:
            mock_icd.return_value = (True, "icloud", canonical_path)
            with _patch_chunk_hash("abc123"):
                pipeline._handle_cloud_duplicate(path)

        pipeline._backend.insert_revision.assert_not_called()
        pipeline._backend.upsert_document.assert_not_called()


class TestCloudDuplicateDifferentHash:
    """_handle_cloud_duplicate — different hash → conflict rename + ingest."""

    def test_different_hash_renames_to_conflict(self):
        """Hash differs → path renamed to conflict_filename(...)."""
        pipeline = _make_pipeline(host_id="macA")
        path = MagicMock(spec=Path)
        path.read_text.return_value = "conflict content"
        canonical_path = MagicMock(spec=Path)
        canonical_path.exists.return_value = True
        canonical_path.read_text.return_value = "canonical content"

        with patch("corpus_forge.sync.push.is_cloud_duplicate") as mock_icd:
            mock_icd.return_value = (True, "icloud", canonical_path)
            with patch("corpus_forge.sync.push.detect_cloud_provider") as mock_dcp:
                mock_dcp.return_value = "icloud"
                with patch("corpus_forge.sync.push.conflict_filename") as mock_cf:
                    conflict = Path("/vault/doc.conflict-macA-icloud-20260508T120000Z.md")
                    mock_cf.return_value = conflict
                    with patch("corpus_forge.sync.push.chunk_content_hash") as mock_cch:
                        mock_cch.side_effect = ["hash_dup", "hash_original"]
                        pipeline._handle_cloud_duplicate(path)

        path.rename.assert_called_once_with(conflict)

    def test_conflict_ingested_as_revision(self):
        """Conflict file inserted as revision via backend."""
        pipeline = _make_pipeline(host_id="macA")
        path = MagicMock(spec=Path)
        path.read_text.return_value = "conflict content"
        canonical_path = MagicMock(spec=Path)
        canonical_path.exists.return_value = True
        canonical_path.read_text.return_value = "canonical content"
        mock_lock = MagicMock()
        mock_lock.__enter__ = MagicMock(return_value=None)
        mock_lock.__exit__ = MagicMock(return_value=None)

        with patch("corpus_forge.sync.push.is_cloud_duplicate") as mock_icd:
            mock_icd.return_value = (True, "icloud", canonical_path)
            with patch("corpus_forge.sync.push.detect_cloud_provider") as mock_dcp:
                mock_dcp.return_value = "icloud"
                with patch("corpus_forge.sync.push.conflict_filename") as mock_cf:
                    mock_cf.return_value = Path(
                        "/vault/doc.conflict-macA-icloud-20260508T120000Z.md"
                    )
                    with patch("corpus_forge.sync.push.chunk_content_hash") as mock_cch:
                        mock_cch.side_effect = ["hash_dup", "hash_original"]
                        pipeline._backend.lock_source.return_value = mock_lock
                        pipeline._backend.resolve_document.return_value = {"id": 42}
                        pipeline._backend.latest_revision.return_value = {
                            "id": 5,
                            "revision_number": 3,
                            "content_hash": "old",
                        }
                        pipeline._handle_cloud_duplicate(path)

        pipeline._backend.insert_revision.assert_called_once()
        pipeline._backend.upsert_document.assert_called_once()
        call_kwargs = pipeline._backend.insert_revision.call_args[1]
        assert call_kwargs["is_tombstone"] is False
        assert call_kwargs["content_hash"] == "hash_dup"
        assert call_kwargs["document_id"] == 42


class TestCloudDuplicateNotDuplicate:
    """_handle_cloud_duplicate — not a match → returns False, no-op."""

    def test_returns_false_when_not_duplicate(self):
        """is_cloud_duplicate returns (False, None, None) → return False."""
        pipeline = _make_pipeline()
        path = MagicMock(spec=Path)

        with patch("corpus_forge.sync.push.is_cloud_duplicate") as mock_icd:
            mock_icd.return_value = (False, None, None)
            result = pipeline._handle_cloud_duplicate(path)

        assert result is False

    def test_no_action_when_canonical_missing(self):
        """is_cloud_duplicate matches but canonical_path does not exist → False."""
        pipeline = _make_pipeline()
        path = MagicMock(spec=Path)
        canonical_path = MagicMock(spec=Path)
        canonical_path.exists.return_value = False

        with patch("corpus_forge.sync.push.is_cloud_duplicate") as mock_icd:
            mock_icd.return_value = (True, "icloud", canonical_path)
            result = pipeline._handle_cloud_duplicate(path)

        assert result is False
        path.unlink.assert_not_called()


# ── P1-21: Tombstone-on-delete (handle_delete) ──────────────────────────


class TestHandleDeleteTombstone:
    """handle_delete — no .icloud sibling → insert tombstone rev + set_tombstone."""

    def test_inserts_tombstone_revision(self):
        """insert_revision called with is_tombstone=True, text='', content_hash=sha256(b'')."""
        pipeline = _make_pipeline(host_id="macA")
        path = MagicMock(spec=Path)
        path.resolve.return_value = Path("/vault/doc.md")
        type(path).name = PropertyMock(return_value="doc.md")
        icloud_sibling = MagicMock(spec=Path)
        icloud_sibling.exists.return_value = False
        path.with_name.return_value = icloud_sibling
        mock_lock = MagicMock()
        mock_lock.__enter__ = MagicMock(return_value=None)
        mock_lock.__exit__ = MagicMock(return_value=None)
        pipeline._backend.lock_source.return_value = mock_lock
        pipeline._backend.resolve_document.return_value = {"id": 42}
        pipeline._backend.latest_revision.return_value = {
            "id": 5,
            "revision_number": 3,
            "content_hash": "old",
        }

        pipeline.handle_delete(path)

        pipeline._backend.insert_revision.assert_called_once()
        call_kwargs = pipeline._backend.insert_revision.call_args[1]
        assert call_kwargs["is_tombstone"] is True
        assert call_kwargs["text"] == ""
        assert call_kwargs["content_hash"] == sha256(b"").hexdigest()
        assert call_kwargs["author_host"] == "macA"

    def test_sets_tombstone_on_document(self):
        """set_tombstone(document_id) called after tombstone revision."""
        pipeline = _make_pipeline(host_id="macA")
        path = MagicMock(spec=Path)
        path.resolve.return_value = Path("/vault/doc.md")
        type(path).name = PropertyMock(return_value="doc.md")
        icloud_sibling = MagicMock(spec=Path)
        icloud_sibling.exists.return_value = False
        path.with_name.return_value = icloud_sibling
        mock_lock = MagicMock()
        mock_lock.__enter__ = MagicMock(return_value=None)
        mock_lock.__exit__ = MagicMock(return_value=None)
        pipeline._backend.lock_source.return_value = mock_lock
        pipeline._backend.resolve_document.return_value = {"id": 42}
        pipeline._backend.latest_revision.return_value = {
            "id": 5,
            "revision_number": 3,
            "content_hash": "old",
        }

        pipeline.handle_delete(path)

        pipeline._backend.set_tombstone.assert_called_once_with(42)

    def test_tombstone_uses_latest_as_parent(self):
        """Tombstone parent_revision_id equals latest.id."""
        pipeline = _make_pipeline(host_id="macA")
        path = MagicMock(spec=Path)
        path.resolve.return_value = Path("/vault/doc.md")
        type(path).name = PropertyMock(return_value="doc.md")
        icloud_sibling = MagicMock(spec=Path)
        icloud_sibling.exists.return_value = False
        path.with_name.return_value = icloud_sibling
        mock_lock = MagicMock()
        mock_lock.__enter__ = MagicMock(return_value=None)
        mock_lock.__exit__ = MagicMock(return_value=None)
        pipeline._backend.lock_source.return_value = mock_lock
        pipeline._backend.resolve_document.return_value = {"id": 42}
        pipeline._backend.latest_revision.return_value = {
            "id": 5,
            "revision_number": 3,
            "content_hash": "old",
        }

        pipeline.handle_delete(path)

        call_kwargs = pipeline._backend.insert_revision.call_args[1]
        assert call_kwargs["parent_revision_id"] == 5

    def test_tombstone_without_prior_revision(self):
        """No latest revision → parent_revision_id is None."""
        pipeline = _make_pipeline(host_id="macA")
        path = MagicMock(spec=Path)
        path.resolve.return_value = Path("/vault/doc.md")
        type(path).name = PropertyMock(return_value="doc.md")
        icloud_sibling = MagicMock(spec=Path)
        icloud_sibling.exists.return_value = False
        path.with_name.return_value = icloud_sibling
        mock_lock = MagicMock()
        mock_lock.__enter__ = MagicMock(return_value=None)
        mock_lock.__exit__ = MagicMock(return_value=None)
        pipeline._backend.lock_source.return_value = mock_lock
        pipeline._backend.resolve_document.return_value = {"id": 42}
        pipeline._backend.latest_revision.return_value = None

        pipeline.handle_delete(path)

        call_kwargs = pipeline._backend.insert_revision.call_args[1]
        assert call_kwargs["parent_revision_id"] is None

    def test_acquires_lock_source(self):
        """lock_source acquired for the deleted path."""
        pipeline = _make_pipeline(host_id="macA")
        path = MagicMock(spec=Path)
        path.resolve.return_value = Path("/vault/doc.md")
        type(path).name = PropertyMock(return_value="doc.md")
        icloud_sibling = MagicMock(spec=Path)
        icloud_sibling.exists.return_value = False
        path.with_name.return_value = icloud_sibling
        mock_lock = MagicMock()
        mock_lock.__enter__ = MagicMock(return_value=None)
        mock_lock.__exit__ = MagicMock(return_value=None)
        pipeline._backend.lock_source.return_value = mock_lock
        pipeline._backend.resolve_document.return_value = {"id": 42}
        pipeline._backend.latest_revision.return_value = {
            "id": 5,
            "revision_number": 3,
            "content_hash": "old",
        }

        pipeline.handle_delete(path)

        pipeline._backend.lock_source.assert_called_once_with("/vault/doc.md")
        mock_lock.__enter__.assert_called_once()
        mock_lock.__exit__.assert_called_once()


class TestHandleDeleteICloudSibling:
    """handle_delete — .icloud sibling exists → no-op (eviction)."""

    def test_icloud_sibling_skips_tombstone(self):
        """<path>.icloud exists → no insert_revision or set_tombstone."""
        pipeline = _make_pipeline(host_id="macA")
        path = MagicMock(spec=Path)
        path.resolve.return_value = Path("/vault/doc.md")
        type(path).name = PropertyMock(return_value="doc.md")
        icloud_sibling = MagicMock(spec=Path)
        icloud_sibling.exists.return_value = True
        path.with_name.return_value = icloud_sibling
        mock_lock = MagicMock()
        mock_lock.__enter__ = MagicMock(return_value=None)
        mock_lock.__exit__ = MagicMock(return_value=None)
        pipeline._backend.lock_source.return_value = mock_lock

        pipeline.handle_delete(path)

        pipeline._backend.insert_revision.assert_not_called()
        pipeline._backend.set_tombstone.assert_not_called()

    def test_icloud_sibling_lock_still_acquired(self):
        """lock_source still acquired even when sibling exists."""
        pipeline = _make_pipeline(host_id="macA")
        path = MagicMock(spec=Path)
        path.resolve.return_value = Path("/vault/doc.md")
        type(path).name = PropertyMock(return_value="doc.md")
        icloud_sibling = MagicMock(spec=Path)
        icloud_sibling.exists.return_value = True
        path.with_name.return_value = icloud_sibling
        mock_lock = MagicMock()
        mock_lock.__enter__ = MagicMock(return_value=None)
        mock_lock.__exit__ = MagicMock(return_value=None)
        pipeline._backend.lock_source.return_value = mock_lock

        pipeline.handle_delete(path)

        pipeline._backend.lock_source.assert_called_once()


# ── helpers (private) ───────────────────────────────────────────────────


def _patch_chunk_hash(return_value: str):
    """Patch chunk_content_hash to return a fixed value."""
    import corpus_forge.sync.push as push_mod

    return _patch(push_mod, "chunk_content_hash", return_value=return_value)


def _patch(module, name: str, **kwargs):
    """Thin wrapper around unittest.mock.patch.object."""
    import unittest.mock as um

    return um.patch.object(module, name, **kwargs)
