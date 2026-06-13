"""Unit tests for daemon module."""

import contextlib
import signal
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from corpus_forge.config import (
    BackendConfig,
    Config,
    DaemonConfig,
    DatasetConfig,
    DatasetSourceConfig,
    EmbedderConfig,
)
from corpus_forge.daemon import (
    _maybe_start_embed_drain,
    main,
    run_daemon,
    setup_signal_handlers,
)


@pytest.fixture(autouse=True)
def _restore_signal_handlers():
    """Snapshot + restore SIGINT/SIGTERM handlers per-test.

    Several tests below call ``run_daemon(config)`` without patching
    ``corpus_forge.daemon.signal.signal``, so ``run_daemon`` installs
    its real ``_shutdown`` handler into the running pytest-xdist
    worker process.  The handler calls ``os._exit(0)`` (uncatchable);
    any subsequent test on the same worker that sends SIGINT to its
    own pid (e.g.
    ``tests/diagnostics/test_logs_subcommand.py::TestLogsTailFollow::test_follow_exits_cleanly_on_sigint``)
    would die without this restore — the leaked handler short-circuits
    the ``KeyboardInterrupt`` path the SIGINT test relies on.

    The previous shutdown used ``sys.exit(0)`` which the test runner
    caught as SystemExit, masking the leak — the ``_exit_hard`` switch
    in fix(daemon) made the leak fatal.  Snapshotting before + after
    each test (rather than only restoring after run_daemon-using
    tests) is the safest defensive choice.
    """
    saved = {
        signal.SIGINT: signal.getsignal(signal.SIGINT),
        signal.SIGTERM: signal.getsignal(signal.SIGTERM),
    }
    yield
    for sig, handler in saved.items():
        if handler is not None:
            with contextlib.suppress(Exception):
                signal.signal(sig, handler)


class TestSetupSignalHandlers:
    """Tests for setup_signal_handlers function."""

    def test_setup_handlers_registers_sigint(self):
        """Test that SIGINT handler is registered."""
        with patch("corpus_forge.daemon.signal.signal") as mock_signal:
            setup_signal_handlers()
            assert mock_signal.call_count == 2
            calls = mock_signal.call_args_list
            assert calls[0][0][0] == 2  # SIGINT = 2

    def test_setup_handlers_registers_sigterm(self):
        """Test that SIGTERM handler is registered."""
        with patch("corpus_forge.daemon.signal.signal") as mock_signal:
            setup_signal_handlers()
            calls = mock_signal.call_args_list
            assert calls[1][0][0] == 15  # SIGTERM = 15

    def test_signal_handler_exits_gracefully(self):
        """Test that the signal handler calls sys.exit(0)."""
        with patch("corpus_forge.daemon.signal.signal") as mock_signal:
            setup_signal_handlers()
            handler = mock_signal.call_args_list[0][0][1]  # SIGINT handler

        # Call the handler directly
        with (
            patch("corpus_forge.daemon.sys.exit") as mock_exit,
            patch("corpus_forge.daemon.logging.info"),
        ):
            handler(2, None)
            mock_exit.assert_called_once_with(0)


class TestDaemonMain:
    """Tests for daemon main function."""

    def test_main_loads_config_and_calls_run_daemon(self):
        """``main`` must load ``Config`` and hand it to ``run_daemon``.

        Regression test for the daemon respawn loop bug where ``main``
        called the unimplemented ``ingest_main(once=False)`` stub
        instead of ``run_daemon``, so launchd's ``KeepAlive`` respawned
        the process every ~10s and no sync engines ever started.
        """
        fake_config = MagicMock()
        with (
            patch("corpus_forge.config.Config.load", return_value=fake_config) as mock_load,
            patch("corpus_forge.logging_config.init_logging"),
            patch("corpus_forge.daemon.run_daemon") as mock_run,
            patch("corpus_forge.daemon.time.sleep", side_effect=SystemExit(0)),
            patch("corpus_forge.daemon.logger"),
            patch("corpus_forge.daemon._log_embedder_drift_warning"),
            pytest.raises(SystemExit),
        ):
            main()
        mock_load.assert_called_once()
        mock_run.assert_called_once_with(fake_config)

    def test_main_logs_start(self):
        """Test that main logs startup message."""
        with (
            patch("corpus_forge.config.Config.load", return_value=MagicMock()),
            patch("corpus_forge.logging_config.init_logging"),
            patch("corpus_forge.daemon.run_daemon"),
            patch("corpus_forge.daemon.time.sleep", side_effect=SystemExit(0)),
            patch("corpus_forge.daemon.logger") as mock_logger,
            patch("corpus_forge.daemon._log_embedder_drift_warning"),
            pytest.raises(SystemExit),
        ):
            main()
        mock_logger.info.assert_any_call("Starting corpus-forge daemon...")

    def test_main_blocks_in_sleep_loop_until_signal(self):
        """``main`` must block after ``run_daemon`` returns.

        ``run_daemon`` is non-blocking — it spawns watcher threads and
        returns.  ``main`` must keep the process alive until a signal
        handler raises ``SystemExit``; otherwise the daemon falls
        through to ``sys.exit(0)`` immediately and launchd / systemd
        respawn it in a tight loop.
        """
        with (
            patch("corpus_forge.config.Config.load", return_value=MagicMock()),
            patch("corpus_forge.logging_config.init_logging"),
            patch("corpus_forge.daemon.run_daemon"),
            patch("corpus_forge.daemon.time.sleep", side_effect=SystemExit(0)) as mock_sleep,
            patch("corpus_forge.daemon.logger"),
            patch("corpus_forge.daemon._log_embedder_drift_warning"),
            pytest.raises(SystemExit),
        ):
            main()
        assert mock_sleep.called


class TestDaemonSignalHandling:
    """Tests for signal handling in daemon mode."""

    def test_sigint_calls_exit_zero(self):
        """Test SIGINT triggers clean exit."""
        with patch("corpus_forge.daemon.signal.signal") as mock_signal:
            setup_signal_handlers()
            handler = mock_signal.call_args_list[0][0][1]  # SIGINT handler

        with (
            patch("corpus_forge.daemon.sys.exit") as mock_exit,
            patch("corpus_forge.daemon.logging.info"),
        ):
            handler(2, None)
            mock_exit.assert_called_once_with(0)

    def test_sigterm_calls_exit_zero(self):
        """Test SIGTERM triggers clean exit."""
        with patch("corpus_forge.daemon.signal.signal") as mock_signal:
            setup_signal_handlers()
            handler = mock_signal.call_args_list[1][0][1]  # SIGTERM handler

        with (
            patch("corpus_forge.daemon.sys.exit") as mock_exit,
            patch("corpus_forge.daemon.logging.info"),
        ):
            handler(15, None)
            mock_exit.assert_called_once_with(0)


class TestDaemonOrchestrator:
    """Tests for daemon sync engine orchestration via run_daemon."""

    @staticmethod
    def _config_with(dataset_mocks):
        """Build a config mock whose ``host_id()`` and ``backend`` are concrete."""
        config = MagicMock()
        config.datasets = dataset_mocks
        config.host_id.return_value = "test-host"
        return config

    @staticmethod
    def _backend_with(id_map):
        """Build a backend mock whose ``find_dataset_id_by_name`` maps name → id."""
        backend = MagicMock()
        backend.find_dataset_id_by_name.side_effect = id_map.get
        return backend

    def test_sync_enabled_starts_engine(self):
        """sync_enabled dataset — SyncEngine constructed with resolved id and started."""
        dataset = MagicMock(sync_enabled=True, name="vault")
        dataset.name = "vault"  # MagicMock(name=...) sets the mock's own name attr
        dataset.sources = [MagicMock()]
        config = self._config_with([dataset])
        backend = self._backend_with({"vault": 42})

        with (
            patch("corpus_forge.daemon._get_any_backend", return_value=backend),
            patch("corpus_forge.daemon._source_root", return_value=Path("/fake/vault")),
            patch("corpus_forge.daemon.SyncEngine") as mock_cls,
        ):
            run_daemon(config)
            mock_cls.assert_called_once()
            kwargs = mock_cls.call_args.kwargs
            assert kwargs["dataset_id"] == 42
            assert kwargs["source_root"] == Path("/fake/vault")
            mock_cls.return_value.start.assert_called_once()

    def test_sync_disabled_skips_engine(self):
        """sync_enabled=False — no SyncEngine created."""
        dataset = MagicMock(sync_enabled=False)
        dataset.sources = [MagicMock()]
        config = self._config_with([dataset])
        backend = self._backend_with({"vault": 42})

        with (
            patch("corpus_forge.daemon._get_any_backend", return_value=backend),
            patch("corpus_forge.daemon._source_root", return_value=Path("/fake/vault")),
            patch("corpus_forge.daemon.SyncEngine") as mock_cls,
        ):
            run_daemon(config)
            mock_cls.assert_not_called()

    def test_multiple_sync_datasets_all_started(self):
        """Multiple sync-enabled datasets — each gets the right id."""
        d1 = MagicMock(sync_enabled=True, sources=[MagicMock()])
        d1.name = "vault"
        d2 = MagicMock(sync_enabled=True, sources=[MagicMock()])
        d2.name = "chats"
        config = self._config_with([d1, d2])
        backend = self._backend_with({"vault": 1, "chats": 7})

        with (
            patch("corpus_forge.daemon._get_any_backend", return_value=backend),
            patch("corpus_forge.daemon._source_root", return_value=Path("/fake")),
            patch("corpus_forge.daemon.SyncEngine") as mock_cls,
        ):
            run_daemon(config)
            assert mock_cls.call_count == 2
            ids_passed = [c.kwargs["dataset_id"] for c in mock_cls.call_args_list]
            assert sorted(ids_passed) == [1, 7]

    def test_signal_stops_all_engines(self):
        """SIGINT/SIGTERM handler calls stop() on every engine."""
        d1 = MagicMock(sync_enabled=True, sources=[MagicMock()])
        d1.name = "vault"
        config = self._config_with([d1])
        backend = self._backend_with({"vault": 1})
        engine = MagicMock()

        with (
            patch("corpus_forge.daemon._get_any_backend", return_value=backend),
            patch("corpus_forge.daemon._source_root", return_value=Path("/fake")),
            patch("corpus_forge.daemon.SyncEngine", return_value=engine),
            patch("corpus_forge.daemon.signal.signal") as mock_signal,
            patch("corpus_forge.daemon._exit_hard"),
            patch("corpus_forge.admin.foreground.clear_pid"),
        ):
            run_daemon(config)
            handler = mock_signal.call_args_list[0][0][1]
            handler(None, None)
            engine.stop.assert_called_once()

    def test_signal_does_not_stop_disabled_engines(self):
        """Only running engines are stopped on signal (disabled skipped)."""
        enabled = MagicMock(sync_enabled=True, sources=[MagicMock()])
        enabled.name = "vault"
        disabled = MagicMock(sync_enabled=False, sources=[MagicMock()])
        disabled.name = "chats"
        config = self._config_with([enabled, disabled])
        backend = self._backend_with({"vault": 1})
        engine = MagicMock()

        with (
            patch("corpus_forge.daemon._get_any_backend", return_value=backend),
            patch("corpus_forge.daemon._source_root", return_value=Path("/fake")),
            patch("corpus_forge.daemon.SyncEngine", return_value=engine),
            patch("corpus_forge.daemon.signal.signal") as mock_signal,
            patch("corpus_forge.daemon._exit_hard"),
            patch("corpus_forge.admin.foreground.clear_pid"),
        ):
            run_daemon(config)
            handler = mock_signal.call_args_list[0][0][1]
            handler(None, None)
            engine.stop.assert_called_once()

    def test_signal_stops_engines_in_parallel(self):
        """``_shutdown`` must stop all engines concurrently.

        With N engines and a per-engine stop cost of T (PullPipeline's
        thread.join can take up to 10s while it drains its poll loop),
        a serial loop hits N*T which trivially exceeds the 30s
        SIGTERM→SIGKILL grace under launchd / systemd.  Parallel
        shutdown caps total time at max(individual stop times)
        regardless of N.

        This test pins the parallelism by giving each engine.stop()
        a 0.5s sleep and asserting the whole handler completes in
        well under N*0.5s.
        """
        import time as _time

        n = 6
        datasets = []
        for i in range(n):
            ds = MagicMock(sync_enabled=True, sources=[MagicMock()])
            ds.name = f"d{i}"
            datasets.append(ds)
        config = self._config_with(datasets)
        backend = self._backend_with({f"d{i}": i + 1 for i in range(n)})

        engine = MagicMock()
        engine.stop.side_effect = lambda: _time.sleep(0.5)

        with (
            patch("corpus_forge.daemon._get_any_backend", return_value=backend),
            patch("corpus_forge.daemon._source_root", return_value=Path("/fake")),
            patch("corpus_forge.daemon.SyncEngine", return_value=engine),
            patch("corpus_forge.daemon.signal.signal") as mock_signal,
            patch("corpus_forge.daemon._exit_hard"),
            patch("corpus_forge.admin.foreground.clear_pid"),
        ):
            run_daemon(config)
            handler = mock_signal.call_args_list[0][0][1]

            t0 = _time.monotonic()
            handler(None, None)
            elapsed = _time.monotonic() - t0

        # Serial would take ~n*0.5s = 3.0s.  Parallel should finish
        # under 1.5s even allowing for ThreadPoolExecutor overhead.
        assert engine.stop.call_count == n
        assert elapsed < 1.5, f"expected parallel shutdown, got {elapsed:.2f}s for {n} engines"

    def test_source_without_fs_root_skipped(self):
        """Sources whose plugin has no watchable root → engine skipped, no crash.

        ``_source_root`` returns ``None`` for plugins like ``zotero`` or
        the chat ingesters.  ``run_daemon`` must skip those sources
        cleanly rather than feed ``None`` into ``SyncEngine`` and crash
        on the first watchdog call.
        """
        dataset = MagicMock(sync_enabled=True, sources=[MagicMock()])
        dataset.name = "vault"
        config = self._config_with([dataset])
        backend = self._backend_with({"vault": 5})

        with (
            patch("corpus_forge.daemon._get_any_backend", return_value=backend),
            patch("corpus_forge.daemon._source_root", return_value=None),
            patch("corpus_forge.daemon.SyncEngine") as mock_cls,
        ):
            run_daemon(config)
            mock_cls.assert_not_called()

    def test_unknown_dataset_name_skipped_with_warning(self, caplog):
        """sync_enabled dataset whose name is not in the backend → skip with WARNING.

        Regression for the ``AttributeError: 'DatasetConfig' object has no
        attribute 'id'`` bug surfaced after PR #86 unblocked the
        ``run_daemon`` path: when the user sets ``sync_enabled = true`` on
        a dataset that hasn't been ingested yet, the backend has no row
        for it, so we cannot resolve a dataset id.  The daemon must skip
        cleanly with a WARNING rather than crash.
        """
        import logging as _logging

        dataset = MagicMock(sync_enabled=True, sources=[MagicMock()])
        dataset.name = "uningested"
        config = self._config_with([dataset])
        backend = self._backend_with({})  # empty — no datasets known

        with (
            patch("corpus_forge.daemon._get_any_backend", return_value=backend),
            patch("corpus_forge.daemon._source_root", return_value=Path("/fake")),
            patch("corpus_forge.daemon.SyncEngine") as mock_cls,
            caplog.at_level(_logging.WARNING, logger="corpus_forge.daemon"),
        ):
            run_daemon(config)
            mock_cls.assert_not_called()

        warnings = [r.message for r in caplog.records if r.levelno >= _logging.WARNING]
        assert any("uningested" in m for m in warnings), warnings

    def test_no_backend_skips_all_engines(self, caplog):
        """``_get_any_backend`` returns None → run_daemon is a clean no-op.

        Mirrors the same defensive guard ``_log_embedder_drift_warning``
        uses for setups where the backend isn't reachable at startup.
        """
        dataset = MagicMock(sync_enabled=True, sources=[MagicMock()])
        dataset.name = "vault"
        config = self._config_with([dataset])

        with (
            patch("corpus_forge.daemon._get_any_backend", return_value=None),
            patch("corpus_forge.daemon.SyncEngine") as mock_cls,
        ):
            run_daemon(config)
            mock_cls.assert_not_called()

    def test_source_uri_prefix_passed_to_sync_engine(self):
        """``run_daemon`` derives the source_uri_prefix from the plugin.

        ``FilesystemSource.parse`` writes
        ``filesystem://<root.name>/<rel>``; ``MarkdownVaultSource.parse``
        writes ``vault://<root.name>/<rel>``.  ``PushPipeline`` must use
        the same scheme so ``find_document`` matches and modifications
        take the cheap replication path instead of re-discovering.
        """
        for plugin_name, expected_scheme in (
            ("filesystem", "filesystem"),
            ("markdown_vault", "vault"),
        ):
            ds = MagicMock(sync_enabled=True)
            ds.name = "vault"
            source = MagicMock()
            source.plugin = plugin_name
            ds.sources = [source]
            config = self._config_with([ds])
            backend = self._backend_with({"vault": 1})

            with (
                patch("corpus_forge.daemon._get_any_backend", return_value=backend),
                patch("corpus_forge.daemon._source_root", return_value=Path("/data/Workspace")),
                patch("corpus_forge.daemon.SyncEngine") as mock_cls,
            ):
                run_daemon(config)

            kwargs = mock_cls.call_args.kwargs
            assert kwargs["source_uri_prefix"] == f"{expected_scheme}://Workspace/", (
                f"plugin={plugin_name}: got prefix={kwargs.get('source_uri_prefix')!r}"
            )

    def test_discovery_callback_swallows_lock_contention(self, caplog):
        """``IngestRunInProgressError`` from ingest_one logs DEBUG, not ERROR.

        Actively-edited files (Obsidian autosave, IDE save-on-keystroke)
        fire watchdog events faster than the debouncer's
        ``cancel-and-reschedule`` window can collapse them.  When two
        callbacks race for the per-source advisory lock, the loser
        raises ``IngestRunInProgressError`` — by design, the file is
        in flight.  The next debounced event picks it up.  Logging
        this at ERROR pollutes daemon.log on every save burst; we
        downgrade to DEBUG and return cleanly.
        """
        import logging as _logging

        from corpus_forge.backends.base import IngestRunInProgressError
        from corpus_forge.daemon import _build_discovery_callback

        backend = MagicMock()
        config = MagicMock()
        source_config = MagicMock()
        cb = _build_discovery_callback(config, backend, dataset_id=1, source_config=source_config)

        fake_source = MagicMock()
        fake_source.parse.return_value = MagicMock(source_uri="filesystem://Workspace/hot.md")

        with (
            patch("corpus_forge.ingest._instantiate_source", return_value=fake_source),
            patch("corpus_forge.ingest.get_chunker_for_source", return_value=MagicMock()),
            patch("corpus_forge.ingest.get_active_embedders", return_value=[]),
            patch(
                "corpus_forge.ingest.ingest_one",
                side_effect=IngestRunInProgressError("locked"),
            ),
            caplog.at_level(_logging.DEBUG, logger="corpus_forge.daemon"),
        ):
            cb(Path("/Workspace/hot.md"))

        # No ERROR-level record for the contention case.
        errors = [r for r in caplog.records if r.levelno >= _logging.ERROR]
        assert not errors, [r.message for r in errors]
        # And the skip was logged at DEBUG so future triage can grep it.
        assert any("lock_source contention" in r.message for r in caplog.records), [
            r.message for r in caplog.records
        ]

    def test_discovery_callback_wired_into_sync_engine(self):
        """``run_daemon`` builds a discovery callback and passes it to SyncEngine.

        The callback turns watchdog ``on_created`` events for brand-new
        files into per-file ingest invocations, so the daemon picks up
        new content without waiting for a manual ``ingest --once``.
        """
        dataset = MagicMock(sync_enabled=True, sources=[MagicMock()])
        dataset.name = "vault"
        config = self._config_with([dataset])
        backend = self._backend_with({"vault": 7})

        with (
            patch("corpus_forge.daemon._get_any_backend", return_value=backend),
            patch("corpus_forge.daemon._source_root", return_value=Path("/fake")),
            patch("corpus_forge.daemon.SyncEngine") as mock_cls,
        ):
            run_daemon(config)

        kwargs = mock_cls.call_args.kwargs
        assert "discovery_callback" in kwargs
        assert callable(kwargs["discovery_callback"])

    def test_dataset_id_passed_to_sync_engine(self):
        """Pin the SyncEngine kwargs contract — dataset_id is an explicit kw arg.

        Regression for the AttributeError: the broken call site was
        ``SyncEngine(... dataset_config=dataset ...)`` and SyncEngine
        read ``self._dataset_config.id``.  The contract now is that
        ``run_daemon`` passes the int it resolved from the backend.
        """
        dataset = MagicMock(sync_enabled=True)
        dataset.name = "vault"
        source = MagicMock()
        dataset.sources = [source]
        config = self._config_with([dataset])
        backend = self._backend_with({"vault": 99})

        with (
            patch("corpus_forge.daemon._get_any_backend", return_value=backend),
            patch("corpus_forge.daemon._source_root", return_value=Path("/fake")),
            patch("corpus_forge.daemon.SyncEngine") as mock_cls,
        ):
            run_daemon(config)

        kwargs = mock_cls.call_args.kwargs
        assert kwargs["dataset_id"] == 99
        assert kwargs["backend"] is backend
        assert kwargs["host_id"] == "test-host"
        assert kwargs["source"] is source
        assert kwargs["source_root"] == Path("/fake")


class TestDaemonRespectsConfigValidator:
    """B-14 regression: daemon path is gated by Config validator.

    The contract is: the validator runs at Config construction time.
    If a caller builds a Config with the offending sqlite+sync_enabled combo,
    the ValidationError fires before run_daemon is ever called — so run_daemon
    itself doesn't need a duplicate check.

    This class pins that contract. If the validator is ever moved to run_daemon
    instead of Config, these tests will catch the regression.
    """

    _SQLITE_DSN = "~/corpus.db"
    _PG_DSN = "postgresql://user:pass@localhost/db"
    _EXACT_ERROR_MSG = (
        "Cross-host sync requires the postgres backend; SQLite is single-host. "
        "Set sync_enabled = false or switch backend.kind to 'postgres'."
    )

    @staticmethod
    def _make_text_source() -> DatasetSourceConfig:
        return DatasetSourceConfig(
            plugin="markdown_vault",
            vault_root="/tmp/vault",
            chunker="markdown",
        )

    @staticmethod
    def _make_embedder() -> EmbedderConfig:
        return EmbedderConfig(
            name="test-embedder",
            provider="sentence_transformers",
            model_id="test/model",
            dimension=384,
        )

    @staticmethod
    def _minimal_daemon() -> DaemonConfig:
        return DaemonConfig(debounce_seconds=2.0, log_level="INFO", log_format="text")

    def test_config_construction_raises_before_run_daemon(self):
        """sqlite + sync_enabled=True raises ValidationError at Config() call, not in run_daemon.

        The test verifies that:
        1. Config(**bad_kwargs) raises ValidationError.
        2. run_daemon is never reached with a bad Config object.
        """
        bad_kwargs = {
            "backend": BackendConfig(kind="sqlite", dsn=self._SQLITE_DSN),
            "daemon": self._minimal_daemon(),
            "datasets": [
                DatasetConfig(
                    name="vault",
                    kind="text",
                    sync_enabled=True,
                    sources=[self._make_text_source()],
                )
            ],
            "embedders": [self._make_embedder()],
        }

        # The validator must fire during Config construction — before run_daemon.
        with pytest.raises(ValidationError) as exc_info:
            Config(**bad_kwargs)

        assert self._EXACT_ERROR_MSG in str(exc_info.value)

    def test_run_daemon_never_called_with_offending_config(self):
        """run_daemon body is never entered when Config construction raises.

        Confirms ValidationError propagates to the caller, not to run_daemon.
        """
        bad_kwargs = {
            "backend": BackendConfig(kind="sqlite", dsn=self._SQLITE_DSN),
            "daemon": self._minimal_daemon(),
            "datasets": [
                DatasetConfig(
                    name="vault",
                    kind="text",
                    sync_enabled=True,
                    sources=[self._make_text_source()],
                )
            ],
            "embedders": [self._make_embedder()],
        }

        with pytest.raises(ValidationError):
            Config(**bad_kwargs)

        # If we reach here, Config raised — run_daemon was never called.
        # This assertion is a static proof: the ValidationError above is the only
        # path out of Config(**bad_kwargs), so run_daemon can never receive that config.

    def test_valid_sqlite_config_reaches_run_daemon(self):
        """sqlite + sync_enabled=False does NOT raise — run_daemon can be reached.

        Confirms the validator is not over-restrictive: a valid sqlite config
        with sync_enabled=False should construct without error, allowing run_daemon
        to be called normally.
        """
        valid_config = Config(
            backend=BackendConfig(kind="sqlite", dsn=self._SQLITE_DSN),
            daemon=self._minimal_daemon(),
            datasets=[
                DatasetConfig(
                    name="vault",
                    kind="text",
                    sync_enabled=False,
                    sources=[self._make_text_source()],
                )
            ],
            embedders=[self._make_embedder()],
        )
        # Config constructed OK — validator did not fire
        assert valid_config.backend.kind == "sqlite"
        assert valid_config.datasets[0].sync_enabled is False

        # run_daemon should be callable without raising (sync_disabled → no engines started)
        with (
            patch("corpus_forge.daemon.SyncEngine") as mock_cls,
            patch("corpus_forge.daemon.signal.signal"),
        ):
            run_daemon(valid_config)
            mock_cls.assert_not_called()


class TestEmbedDrainWiring:
    """RFC fleet-5 item 2b — ``_maybe_start_embed_drain`` gating + thread."""

    @staticmethod
    def _drain_config(*, embed_drain: bool, backend_kind: str = "postgres"):
        """A config mock with concrete service / backend / embed drain attrs."""
        config = MagicMock()
        config.service.embed_drain = embed_drain
        config.service.ingest_watch = True
        config.backend.kind = backend_kind
        config.embed.drain_idle_min = 5.0
        config.embed.drain_idle_max = 300.0
        return config

    def test_no_backend_no_drain(self):
        cfg = self._drain_config(embed_drain=True)
        with patch("corpus_forge.embed_drain.EmbedDrainLoop") as mock_loop:
            assert _maybe_start_embed_drain(cfg, None, threading.Event()) is None
            mock_loop.assert_not_called()

    def test_embed_drain_off_no_drain(self):
        cfg = self._drain_config(embed_drain=False)
        with patch("corpus_forge.embed_drain.EmbedDrainLoop") as mock_loop:
            assert _maybe_start_embed_drain(cfg, MagicMock(), threading.Event()) is None
            mock_loop.assert_not_called()

    def test_non_postgres_backend_no_drain(self):
        cfg = self._drain_config(embed_drain=True, backend_kind="sqlite")
        with patch("corpus_forge.embed_drain.EmbedDrainLoop") as mock_loop:
            assert _maybe_start_embed_drain(cfg, MagicMock(), threading.Event()) is None
            mock_loop.assert_not_called()

    def test_postgres_drain_starts_daemon_thread(self):
        cfg = self._drain_config(embed_drain=True, backend_kind="postgres")
        backend = MagicMock()
        stop = threading.Event()
        with (
            patch("corpus_forge.embed_drain.EmbedDrainLoop") as mock_loop,
            patch("corpus_forge.daemon.threading.Thread") as mock_thread,
        ):
            thread = _maybe_start_embed_drain(cfg, backend, stop)
            # Loop constructed with the configured idle window.
            mock_loop.assert_called_once()
            kwargs = mock_loop.call_args.kwargs
            assert kwargs["idle_min"] == 5.0
            assert kwargs["idle_max"] == 300.0
            # A daemon thread targeting loop.run(stop_event) was started.
            mock_thread.assert_called_once()
            t_kwargs = mock_thread.call_args.kwargs
            assert t_kwargs["daemon"] is True
            assert t_kwargs["target"] == mock_loop.return_value.run
            assert t_kwargs["args"] == (stop,)
            mock_thread.return_value.start.assert_called_once()
            assert thread is mock_thread.return_value

    def test_loop_construction_failure_is_swallowed(self):
        cfg = self._drain_config(embed_drain=True, backend_kind="postgres")
        with patch("corpus_forge.embed_drain.EmbedDrainLoop", side_effect=RuntimeError("boom")):
            # Must not propagate — a broken drain wiring can't kill the daemon.
            assert _maybe_start_embed_drain(cfg, MagicMock(), threading.Event()) is None


class TestIngestWatchGating:
    """RFC fleet-5 item 2b — ``[service] ingest_watch`` toggles the watcher."""

    @staticmethod
    def _watch_config(*, ingest_watch: bool):
        dataset = MagicMock(sync_enabled=True)
        dataset.name = "vault"
        dataset.sources = [MagicMock()]
        config = MagicMock()
        config.datasets = [dataset]
        config.host_id.return_value = "test-host"
        config.service.ingest_watch = ingest_watch
        config.service.embed_drain = False  # isolate the watcher behaviour
        config.backend.kind = "postgres"
        return config

    def test_ingest_watch_false_skips_engines(self):
        config = self._watch_config(ingest_watch=False)
        backend = MagicMock()
        backend.find_dataset_id_by_name.return_value = 42
        with (
            patch("corpus_forge.daemon._get_any_backend", return_value=backend),
            patch("corpus_forge.daemon._source_root", return_value=Path("/fake/vault")),
            patch("corpus_forge.daemon.SyncEngine") as mock_cls,
            patch("corpus_forge.daemon.signal.signal"),
        ):
            run_daemon(config)
            mock_cls.assert_not_called()

    def test_ingest_watch_true_starts_engine(self):
        config = self._watch_config(ingest_watch=True)
        backend = MagicMock()
        backend.find_dataset_id_by_name.return_value = 42
        with (
            patch("corpus_forge.daemon._get_any_backend", return_value=backend),
            patch("corpus_forge.daemon._source_root", return_value=Path("/fake/vault")),
            patch("corpus_forge.daemon.SyncEngine") as mock_cls,
            patch("corpus_forge.daemon.signal.signal"),
        ):
            run_daemon(config)
            mock_cls.assert_called_once()
