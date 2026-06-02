"""Failing tests for SyncEngine (P1-27)."""

from unittest.mock import MagicMock, patch

from corpus_forge.sync.engine import SyncEngine


def _make_engine(
    dataset_id: int = 1,
    dataset_config=None,
    source=None,
    backend=None,
    embedders=None,
    host_id: str = "test-host",
    daemon_config=None,
    discovery_callback=None,
) -> SyncEngine:
    return SyncEngine(
        dataset_id=dataset_id,
        dataset_config=dataset_config or MagicMock(),
        source=source or MagicMock(),
        backend=backend or MagicMock(),
        embedders=embedders or [],
        host_id=host_id,
        daemon_config=daemon_config or MagicMock(),
        discovery_callback=discovery_callback,
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

    def test_stores_dataset_id_as_int(self):
        """``dataset_id`` is the explicit int handed in by ``run_daemon``.

        Regression: the prior implementation read
        ``self._dataset_config.id`` and crashed with ``AttributeError``
        because Pydantic ``DatasetConfig`` has no ``id`` field.  The
        engine must accept the resolved id as its own kwarg.
        """
        engine = _make_engine(dataset_id=42)
        assert engine._dataset_id == 42


class TestStart:
    """start() creates PushPipeline + PullPipeline and starts both."""

    def test_push_pipeline_created(self):
        engine = _make_engine()
        with (
            patch("corpus_forge.sync.engine.PushPipeline") as mock_pp,
            patch("corpus_forge.sync.engine.PullPipeline"),
        ):
            engine.start()
        mock_pp.assert_called_once()
        mock_pp.return_value.start.assert_called_once()

    def test_pull_pipeline_created(self):
        engine = _make_engine()
        with (
            patch("corpus_forge.sync.engine.PushPipeline"),
            patch("corpus_forge.sync.engine.PullPipeline") as mock_pl,
        ):
            engine.start()
        mock_pl.assert_called_once()
        mock_pl.return_value.start.assert_called_once()

    def test_push_pipeline_started(self):
        engine = _make_engine()
        with (
            patch("corpus_forge.sync.engine.PushPipeline") as mock_pp,
            patch("corpus_forge.sync.engine.PullPipeline"),
        ):
            engine.start()
        mock_pp.return_value.start.assert_called_once()

    def test_pull_pipeline_started(self):
        engine = _make_engine()
        with (
            patch("corpus_forge.sync.engine.PushPipeline"),
            patch("corpus_forge.sync.engine.PullPipeline") as mock_pl,
        ):
            engine.start()
        mock_pl.return_value.start.assert_called_once()

    def test_push_pipeline_receives_discovery_callback(self):
        """SyncEngine forwards ``discovery_callback`` to PushPipeline.

        The callback turns ``handle_change`` calls for brand-new files
        into discovery-and-ingest invocations.  PullPipeline does not
        need it (it consumes revisions written by another host).
        """
        callback = MagicMock()
        engine = _make_engine(dataset_id=1, discovery_callback=callback)
        with (
            patch("corpus_forge.sync.engine.PushPipeline") as mock_push,
            patch("corpus_forge.sync.engine.PullPipeline"),
        ):
            engine.start()
        kwargs = mock_push.call_args.kwargs
        assert kwargs.get("discovery_callback") is callback

    def test_pipelines_receive_resolved_dataset_id(self):
        """Both pipelines must be constructed with the int dataset_id.

        Regression for ``AttributeError: 'DatasetConfig' object has no
        attribute 'id'`` — the engine no longer reaches into the
        Pydantic ``dataset_config`` for the id; it uses the explicit
        ``dataset_id`` it was constructed with.  This pins the wiring
        between ``run_daemon``'s name→id lookup and the per-pipeline
        ``dataset_id`` argument.
        """
        engine = _make_engine(dataset_id=99)
        with (
            patch("corpus_forge.sync.engine.PushPipeline") as mock_push,
            patch("corpus_forge.sync.engine.PullPipeline") as mock_pull,
        ):
            engine.start()
        # PushPipeline(backend, dataset_id, echo, host_id) — id is the 2nd positional
        assert mock_push.call_args.args[1] == 99
        # PullPipeline(backend, dataset_id, source_root, echo, host_id)
        assert mock_pull.call_args.args[1] == 99


class TestStop:
    """stop() stops both pipelines, flushes pending revisions."""

    def test_push_pipeline_stopped(self):
        engine = _make_engine()
        with (
            patch("corpus_forge.sync.engine.PushPipeline") as mock_pp,
            patch("corpus_forge.sync.engine.PullPipeline"),
        ):
            engine.start()
            engine.stop()
        mock_pp.return_value.stop.assert_called_once()

    def test_pull_pipeline_stopped(self):
        engine = _make_engine()
        with (
            patch("corpus_forge.sync.engine.PushPipeline"),
            patch("corpus_forge.sync.engine.PullPipeline") as mock_pl,
        ):
            engine.start()
            engine.stop()
        mock_pl.return_value.stop.assert_called_once()

    def test_stop_is_idempotent_after_start(self):
        # stop() no longer calls upsert_document — it simply stops both
        # pipelines.  Verify stop() completes without error.
        engine = _make_engine()
        with (
            patch("corpus_forge.sync.engine.PushPipeline") as mock_pp,
            patch("corpus_forge.sync.engine.PullPipeline") as mock_pl,
        ):
            engine.start()
            engine.stop()
        mock_pp.return_value.stop.assert_called_once()
        mock_pl.return_value.stop.assert_called_once()
