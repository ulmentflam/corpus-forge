"""Failing tests for SyncEngine (P1-27)."""

from unittest.mock import MagicMock, patch

from corpus_forge.sync.engine import SyncEngine


def _make_engine(
    dataset_config=None,
    source=None,
    backend=None,
    embedders=None,
    host_id: str = "test-host",
    daemon_config=None,
) -> SyncEngine:
    return SyncEngine(
        dataset_config=dataset_config or MagicMock(),
        source=source or MagicMock(),
        backend=backend or MagicMock(),
        embedders=embedders or [],
        host_id=host_id,
        daemon_config=daemon_config or MagicMock(),
    )


class TestConstructor:
    """SyncEngine constructor stores all config."""

    def test_stores_dataset_config(self):
        engine = _make_engine()
        assert engine._dataset_config is not None

    def test_stores_source(self):
        engine = _make_engine()
        assert engine._source is not None

    def test_stores_backend(self):
        engine = _make_engine()
        assert engine._backend is not None

    def test_stores_embedders(self):
        engine = _make_engine()
        assert engine._embedders is not None

    def test_stores_host_id(self):
        engine = _make_engine(host_id="macA")
        assert engine._host_id == "macA"

    def test_stores_daemon_config(self):
        engine = _make_engine()
        assert engine._daemon_config is not None


class TestStart:
    """start() creates PushPipeline + PullPipeline and starts both."""

    def test_push_pipeline_created(self):
        engine = _make_engine()
        with patch("corpus_forge.sync.engine.PushPipeline") as mock_pp:
            with patch("corpus_forge.sync.engine.PullPipeline") as mock_pl:
                engine.start()
        mock_pp.assert_called_once()
        mock_pp.return_value.start.assert_called_once()

    def test_pull_pipeline_created(self):
        engine = _make_engine()
        with patch("corpus_forge.sync.engine.PushPipeline") as mock_pp:
            with patch("corpus_forge.sync.engine.PullPipeline") as mock_pl:
                engine.start()
        mock_pl.assert_called_once()
        mock_pl.return_value.start.assert_called_once()

    def test_push_pipeline_started(self):
        engine = _make_engine()
        with patch("corpus_forge.sync.engine.PushPipeline") as mock_pp:
            with patch("corpus_forge.sync.engine.PullPipeline") as mock_pl:
                engine.start()
        mock_pp.return_value.start.assert_called_once()

    def test_pull_pipeline_started(self):
        engine = _make_engine()
        with patch("corpus_forge.sync.engine.PushPipeline") as mock_pp:
            with patch("corpus_forge.sync.engine.PullPipeline") as mock_pl:
                engine.start()
        mock_pl.return_value.start.assert_called_once()


class TestStop:
    """stop() stops both pipelines, flushes pending revisions."""

    def test_push_pipeline_stopped(self):
        engine = _make_engine()
        with patch("corpus_forge.sync.engine.PushPipeline") as mock_pp:
            with patch("corpus_forge.sync.engine.PullPipeline"):
                engine.start()
                engine.stop()
        mock_pp.return_value.stop.assert_called_once()

    def test_pull_pipeline_stopped(self):
        engine = _make_engine()
        with patch("corpus_forge.sync.engine.PushPipeline") as mock_pp:
            with patch("corpus_forge.sync.engine.PullPipeline") as mock_pl:
                engine.start()
                engine.stop()
        mock_pl.return_value.stop.assert_called_once()

    def test_flushes_pending_revisions(self):
        engine = _make_engine()
        with patch("corpus_forge.sync.engine.PushPipeline"):
            with patch("corpus_forge.sync.engine.PullPipeline"):
                engine.start()
                engine.stop()
        engine._backend.upsert_document.assert_called_once()
