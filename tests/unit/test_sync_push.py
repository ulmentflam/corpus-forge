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
    discovery_callback=None,
) -> PushPipeline:
    backend = backend or MagicMock()
    echo_suppressor = echo_suppressor or MagicMock()
    return PushPipeline(
        backend=backend,
        dataset_id=dataset_id,
        echo_suppressor=echo_suppressor,
        host_id=host_id,
        discovery_callback=discovery_callback,
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
    """Step 3b — local content_hash differs from latest → insert_revision called."""

    def test_inserts_revision_and_upserts_document(self, mock_path, mock_lock):
        """Different hash → insert_revision called; upsert_document replaced by direct UPDATE."""
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

        # source_uri is now included in insert_revision call (BUG-5 fix)
        call_kwargs = pipeline._backend.insert_revision.call_args[1]
        assert call_kwargs["document_id"] == 42
        assert call_kwargs["content_hash"] == "first_hash"
        assert call_kwargs["text"] == "hello world"
        assert call_kwargs["parent_revision_id"] is None
        assert call_kwargs["author_host"] == "macA"
        assert call_kwargs["is_tombstone"] is False


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

    def test_lock_source_released_on_exception(self, mock_path, mock_lock, caplog):
        """When insert_revision raises, lock_source is still released.

        ``handle_change`` runs inside a ``threading.Timer`` callback, so
        it now catches and logs exceptions instead of re-raising — a
        bare propagation would land on stderr (``/dev/null`` for the
        LaunchAgent) and silently kill watchdog event delivery.  The
        ``with backend.lock_source(...):`` block still releases the
        lock because Python's context manager semantics fire ``__exit__``
        on the exception path regardless.
        """
        import logging as _logging  # noqa: PLC0415

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

        with (
            _patch_chunk_hash("new_hash"),
            caplog.at_level(_logging.ERROR, logger="corpus_forge.sync.push"),
        ):
            # Must NOT propagate — watchdog thread must survive.
            pipeline.handle_change(mock_path)

        mock_lock.__exit__.assert_called_once()
        # The exception is captured in the rotating log.
        assert any("db error" in r.message or "handle_change raised" in r.message for r in caplog.records), caplog.records

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

    def test_stop_without_start_is_noop(self):
        """Calling stop() before start() is a safe no-op (not an error)."""
        pipeline = _make_pipeline()
        # Should not raise — stop on unstarted pipeline is a no-op after BUG-7 fix
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

    @pytest.mark.requires_unix
    def test_different_paths_independent(self):
        """``requires_unix``: the test compares timer-keys against POSIX
        path strings (``/vault/a.md``). Windows yields ``\\vault\\a.md``
        and the dict-key lookup breaks. The plumbing itself is OS-
        agnostic; this test would need a portable path fixture to run
        on Windows."""
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
        pipeline._backend.find_document.return_value = {"id": 42}
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

    @pytest.mark.requires_unix
    def test_acquires_lock_source(self):
        """lock_source acquired for the deleted path.

        ``requires_unix``: the mocked ``path.resolve()`` returns
        ``Path("/vault/doc.md")`` which on Windows stringifies to
        ``\\vault\\doc.md``, breaking the literal-string assertion
        ``lock_source.assert_called_once_with("/vault/doc.md")``.
        """
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


# ── Binary-file robustness ─────────────────────────────────────────────


class TestSourceUriPrefix:
    """``PushPipeline`` must produce the same URI scheme ``Source.parse`` writes.

    Filesystem sources land documents in ``corpus.documents`` with
    ``source_uri = "filesystem://<root.name>/<relpath>"`` (see
    ``corpus_forge.sources.filesystem.FilesystemSource.parse``).
    Markdown-vault sources use ``"vault://<root.name>/<relpath>"``.
    Without a matching prefix, ``PushPipeline._compute_source_uri``
    returns a bare relative path and ``find_document`` never matches —
    every file modification fires the discovery callback again,
    re-running the entire embedder pipeline (~2 min) for what should
    be a fast revision insert.
    """

    def test_default_prefix_is_relative_path(self, tmp_path):
        """No prefix → legacy bare-relpath behaviour (back-compat for integration tests)."""
        pipeline = _make_pipeline()
        pipeline._source_root = tmp_path
        pipeline._source_uri_prefix = ""

        file_path = tmp_path / "notes" / "foo.md"
        file_path.parent.mkdir()
        file_path.write_text("hi", encoding="utf-8")

        assert pipeline._compute_source_uri(file_path) == "notes/foo.md"

    def test_filesystem_prefix_matches_source_parse(self, tmp_path):
        """``filesystem://<root>/<rel>`` matches ``FilesystemSource.parse``."""
        root = tmp_path / "Workspace"
        root.mkdir()
        pipeline = _make_pipeline()
        pipeline._source_root = root
        pipeline._source_uri_prefix = f"filesystem://{root.name}/"

        file_path = root / "notes" / "foo.md"
        file_path.parent.mkdir()
        file_path.write_text("hi", encoding="utf-8")

        assert pipeline._compute_source_uri(file_path) == "filesystem://Workspace/notes/foo.md"

    def test_vault_prefix_matches_markdown_vault_parse(self, tmp_path):
        """``vault://<root>/<rel>`` matches ``MarkdownVaultSource.parse``."""
        root = tmp_path / "Vault"
        root.mkdir()
        pipeline = _make_pipeline()
        pipeline._source_root = root
        pipeline._source_uri_prefix = f"vault://{root.name}/"

        file_path = root / "daily" / "2026-06-02.md"
        file_path.parent.mkdir()
        file_path.write_text("hi", encoding="utf-8")

        assert pipeline._compute_source_uri(file_path) == "vault://Vault/daily/2026-06-02.md"

    def test_pipeline_start_accepts_source_uri_prefix(self, tmp_path):
        """``PushPipeline.start`` plumbs the prefix into the instance."""
        backend = MagicMock()
        pipeline = PushPipeline(
            backend=backend,
            dataset_id=1,
            echo_suppressor=MagicMock(),
            host_id="h",
        )
        with patch("corpus_forge.sync.push.observers.Observer"):
            pipeline.start(
                source_root=tmp_path,
                exclude_globs=[],
                source_uri_prefix=f"filesystem://{tmp_path.name}/",
            )
        try:
            assert pipeline._source_uri_prefix == f"filesystem://{tmp_path.name}/"
        finally:
            pipeline._observer = None
            pipeline._handler = None


class TestBinaryFileHandling:
    """``handle_change`` must survive non-UTF-8 binary files cleanly.

    Watchdog fires for every file under the watched root, including
    binaries (JPEGs in ``tool-results/``, vendored archives, etc.).
    ``read_text(encoding="utf-8")`` raises ``UnicodeDecodeError`` on
    these.  Two layers of defense:

    1. ``_should_ignore`` consults the same ``IgnoreStack`` the scanner
       uses (``load_global_ignore`` + ``load_local_ignore`` + globs),
       so any file the ``.corpusignore`` managed block excludes is
       dropped before we touch its bytes.
    2. ``handle_change`` catches ``UnicodeDecodeError`` for the
       residual binaries that slip through the ignore stack, logs at
       DEBUG, and returns cleanly — no ERROR-level noise, no stack
       trace in ``daemon.log`` on every JPEG event.
    """

    def test_handle_change_swallows_unicode_decode_error(self, mock_path, mock_lock, caplog):
        """A non-UTF-8 binary file must not raise to the watchdog timer."""
        import logging as _logging  # noqa: PLC0415

        # Make read_text simulate a binary file's first byte.
        mock_path.read_text.side_effect = UnicodeDecodeError(
            "utf-8", b"\xff", 0, 1, "invalid start byte"
        )
        type(mock_path.stat.return_value).st_mtime = PropertyMock(return_value=1000.0)
        backend = MagicMock()
        backend.lock_source.return_value = mock_lock
        pipeline = _make_pipeline(backend=backend)
        pipeline._echo_suppressor.was_just_written.return_value = False
        pipeline._source_root = Path("/vault")

        with caplog.at_level(_logging.DEBUG, logger="corpus_forge.sync.push"):
            # Must not raise.
            pipeline.handle_change(mock_path)

        # No replication-side calls fired — we bailed before the lock.
        backend.resolve_document.assert_not_called()
        backend.insert_revision.assert_not_called()
        # And the error did NOT escalate to ERROR-level "handle_change raised"
        # (that path is for genuine bugs; binary files are expected noise).
        errors = [r for r in caplog.records if r.levelno >= _logging.ERROR]
        assert not errors, [r.message for r in errors]

    def test_should_ignore_consults_ignore_stack(self, tmp_path):
        """When an ``IgnoreStack`` is wired, it gates ``_should_ignore``.

        The scanner uses ``IgnoreStack`` (``load_global_ignore +
        load_local_ignore + exclude_globs``) at scan time; the daemon's
        watchdog needs the same gate so the two views can't drift.
        """
        from corpus_forge.ignore import CorpusIgnore, IgnoreStack

        pipeline = _make_pipeline()
        pipeline._source_root = tmp_path
        stack = IgnoreStack(sets=(CorpusIgnore.from_lines(["*.jpg"], root=tmp_path),))
        pipeline._ignore_stack = stack

        jpg_path = tmp_path / "img" / "cat.jpg"
        jpg_path.parent.mkdir()
        jpg_path.write_bytes(b"\xff\xd8\xff")

        assert pipeline._should_ignore(jpg_path) is True

    def test_should_ignore_passes_text_files_through_stack(self, tmp_path):
        """An ``IgnoreStack`` whose patterns don't match must NOT block."""
        from corpus_forge.ignore import CorpusIgnore, IgnoreStack

        pipeline = _make_pipeline()
        pipeline._source_root = tmp_path
        stack = IgnoreStack(sets=(CorpusIgnore.from_lines(["*.jpg"], root=tmp_path),))
        pipeline._ignore_stack = stack

        md_path = tmp_path / "notes" / "foo.md"
        md_path.parent.mkdir()
        md_path.write_text("# hello", encoding="utf-8")

        assert pipeline._should_ignore(md_path) is False

    def test_pipeline_builds_ignore_stack_from_managed_corpusignore(self, tmp_path):
        """``PushPipeline.start`` composes the same IgnoreStack the scanner uses.

        Walks the same three-layer composition: global ignore +
        ``<source_root>/.corpusignore`` (the managed block) +
        ``exclude_globs``.  Whatever ``.corpusignore`` patterns the user's
        managed block carries (audio/video when whisper is off,
        RAW images when image_extractor is off, etc.) MUST gate the
        daemon's watchdog the same way they gate ``ingest --once``.
        """
        # Drop a ``.corpusignore`` whose body matches ``*.png``.
        (tmp_path / ".corpusignore").write_text("*.png\n", encoding="utf-8")

        backend = MagicMock()
        backend.resolve_document.return_value = None
        pipeline = PushPipeline(
            backend=backend,
            dataset_id=1,
            echo_suppressor=MagicMock(),
            host_id="h",
            discovery_callback=None,
        )
        # ``start`` schedules a watchdog Observer — patch it out so the
        # test doesn't spawn real OS threads.
        with patch("corpus_forge.sync.push.observers.Observer"):
            pipeline.start(source_root=tmp_path, exclude_globs=[])
        try:
            assert pipeline._ignore_stack is not None
            png_path = tmp_path / "img.png"
            png_path.write_bytes(b"\x89PNG\r\n")
            assert pipeline._should_ignore(png_path) is True
        finally:
            # No-op stop (Observer was a MagicMock) — keeps test hermetic.
            pipeline._observer = None
            pipeline._handler = None


# ── New-file discovery callback ─────────────────────────────────────────


class TestDiscoveryCallback:
    """``handle_change`` invokes ``discovery_callback`` for brand-new files.

    Without the callback, the pipeline silently drops files that have no
    backing ``corpus.documents`` row — by design, since PushPipeline is
    a *replication* pipeline.  ``run_daemon`` injects a callback that
    runs the per-file ingest path (extract → chunk → upsert → embed)
    so brand-new files on disk enter the corpus on first watchdog
    event rather than waiting for a manual ``corpus-forge ingest --once``.
    """

    @staticmethod
    def _arm_pipeline(callback, mock_path, mock_lock, find_returns, resolve_returns=None):
        """Build a pipeline whose echo suppressor + stat are happy.

        ``find_returns`` controls the new-file branch (None → discovery).
        ``resolve_returns`` controls the replication branch (used when
        the discovery path is skipped — i.e. the doc already exists).
        """
        backend = MagicMock()
        backend.find_document.return_value = find_returns
        backend.resolve_document.return_value = resolve_returns or find_returns
        backend.latest_revision.return_value = None
        backend.lock_source.return_value = mock_lock
        pipeline = _make_pipeline(backend=backend, discovery_callback=callback)
        pipeline._echo_suppressor.was_just_written.return_value = False
        pipeline._source_root = Path("/vault")
        type(mock_path.stat.return_value).st_mtime = PropertyMock(return_value=1000.0)
        return pipeline, backend

    def test_callback_fires_when_document_not_in_backend(self, mock_path, mock_lock):
        """``resolve_document`` returns None → discovery callback called."""
        callback = MagicMock()
        pipeline, _backend = self._arm_pipeline(callback, mock_path, mock_lock, None)

        with _patch_chunk_hash("h1"):
            pipeline.handle_change(mock_path)

        callback.assert_called_once_with(mock_path)

    def test_callback_fires_when_content_hash_differs(self, mock_path, mock_lock):
        """Existing file with changed content → discovery callback fires.

        Without this, the replication path's ``UPDATE corpus.documents
        SET text`` would land the new text in the documents row but
        leave the chunks + embeddings stale, so semantic search
        would keep returning the OLD content.  ``ingest_one`` (called
        via the discovery callback) is the only path that re-runs
        the chunker + embedder + ``upsert_document`` (BUG-3 fix
        preserves chunks whose hash didn't change).
        """
        callback = MagicMock()
        pipeline, _backend = self._arm_pipeline(
            callback,
            mock_path,
            mock_lock,
            {"id": 1, "content_hash": "old-hash"},
        )

        with _patch_chunk_hash("new-hash"):
            pipeline.handle_change(mock_path)

        callback.assert_called_once_with(mock_path)

    def test_callback_skipped_when_content_hash_matches(self, mock_path, mock_lock):
        """Existing file with matching content_hash → no-op, no callback.

        Watchdog mtime-touch / IDE format-on-save fires events for
        files whose content didn't actually change.  Routing those
        through the discovery callback would waste embedder cycles
        on no-ops.  ``find_document`` returning the matching hash
        is the cheap pre-check.
        """
        callback = MagicMock()
        pipeline, _backend = self._arm_pipeline(
            callback,
            mock_path,
            mock_lock,
            {"id": 1, "content_hash": "h1"},
        )

        with _patch_chunk_hash("h1"):
            pipeline.handle_change(mock_path)

        callback.assert_not_called()

    def test_no_callback_preserves_silent_drop(self, mock_path, mock_lock):
        """``discovery_callback=None`` keeps legacy behavior: silent return."""
        pipeline, backend = self._arm_pipeline(None, mock_path, mock_lock, None)

        # Must not raise.  No further backend calls past resolve_document.
        with _patch_chunk_hash("h1"):
            pipeline.handle_change(mock_path)

        backend.insert_revision.assert_not_called()
        backend.upsert_document.assert_not_called()

    def test_callback_exception_does_not_kill_pipeline(self, mock_path, mock_lock):
        """A buggy discovery callback must not crash the watchdog thread.

        Exceptions inside the callback are logged + swallowed.  The
        next file event proceeds normally.
        """
        callback = MagicMock(side_effect=RuntimeError("callback bug"))
        pipeline, _backend = self._arm_pipeline(callback, mock_path, mock_lock, None)

        # Should not raise.
        with _patch_chunk_hash("h1"):
            pipeline.handle_change(mock_path)
        callback.assert_called_once()


# ── helpers (private) ───────────────────────────────────────────────────


def _patch_chunk_hash(return_value: str):
    """Patch chunk_content_hash to return a fixed value."""
    import corpus_forge.sync.push as push_mod

    return _patch(push_mod, "chunk_content_hash", return_value=return_value)


def _patch(module, name: str, **kwargs):
    """Thin wrapper around unittest.mock.patch.object."""
    import unittest.mock as um

    return um.patch.object(module, name, **kwargs)
