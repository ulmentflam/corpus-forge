"""Unit tests for the daemon's federation drift checker (RFC fleet-3 item 4).

The daemon detects shared-config drift and logs ONE WARNING pointing the
operator at ``corpus-forge config pull`` — it NEVER applies anything (RFC
non-goal: "No auto-apply ... no background config mutation, ever").

The contract pinned here:

- With the default config (``federation.enabled=False``), the daemon
  constructs NO drift checker — ``get_shared_config`` is never called
  across N loop ticks (the hard backcompat bar).
- When enabled + backend is postgres + published version > last-pulled,
  exactly one WARNING fires.
- No warning when published == last-pulled, or when nothing is published.
- The check is throttled by ``drift_check_interval_s``: two wakeups
  inside one interval ⇒ one backend read.
- Every failure mode (backend unreachable, ``get_shared_config`` raising,
  FederationUnsupported) is silent — no warning, no crash.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from corpus_forge.backends.base import FederationUnsupported
from corpus_forge.config import (
    BackendConfig,
    Config,
    DaemonConfig,
    DatasetConfig,
    DatasetSourceConfig,
    FederationConfig,
)
from corpus_forge.daemon import _make_federation_drift_checker, main


def _make_config(
    *, federation: FederationConfig | None = None, backend_kind: str = "postgres"
) -> Config:
    """Build a minimal valid Config with a configurable [federation] block."""
    dsn = "postgresql://u:p@localhost/db" if backend_kind == "postgres" else "/tmp/x.db"
    return Config(
        backend=BackendConfig(kind=backend_kind, dsn=dsn),
        daemon=DaemonConfig(),
        datasets=[
            DatasetConfig(
                name="ds",
                kind="text",
                sources=[
                    DatasetSourceConfig(
                        plugin="markdown_vault",
                        vault_root="~/v",
                        chunker="markdown",
                    )
                ],
            )
        ],
        federation=federation or FederationConfig(),
    )


class TestCheckerConstruction:
    """When the checker is (not) constructed at all."""

    def test_default_config_constructs_no_checker(self) -> None:
        # The hard backcompat bar: enabled=False ⇒ no checker object.
        config = _make_config()
        assert _make_federation_drift_checker(config) is None

    def test_sqlite_backend_constructs_no_checker(self) -> None:
        # Federation requires postgres; SQLite is single-host.
        config = _make_config(federation=FederationConfig(enabled=True), backend_kind="sqlite")
        assert _make_federation_drift_checker(config) is None

    def test_enabled_postgres_constructs_a_checker(self) -> None:
        config = _make_config(federation=FederationConfig(enabled=True))
        assert _make_federation_drift_checker(config) is not None


class TestBackcompatNoSharedConfigRead:
    """With the default config, NO loop tick ever reads shared config."""

    def test_default_config_never_calls_get_shared_config(self) -> None:
        config = _make_config()
        checker = _make_federation_drift_checker(config)
        assert checker is None

        # Simulate N loop ticks the way ``main`` would: a None checker is
        # simply never invoked. Assert via a backend mock that nothing
        # reads shared config across the ticks.
        backend = MagicMock()
        with patch("corpus_forge.daemon._get_any_backend", return_value=backend):
            for _ in range(5):
                if checker is not None:  # pragma: no cover — defensive
                    checker()
        backend.get_shared_config.assert_not_called()

    def test_main_default_config_builds_no_checker(self) -> None:
        """``main`` with a default config constructs no drift checker.

        End-to-end through ``main``: a frozen default-config (federation
        off) means ``_make_federation_drift_checker`` returns ``None`` and
        the blocking loop sleeps 3600s without reading shared config.
        """
        config = _make_config()  # federation disabled (default)
        with (
            patch("corpus_forge.config.Config.load", return_value=config),
            patch("corpus_forge.logging_config.init_logging"),
            patch("corpus_forge.daemon.run_daemon"),
            patch("corpus_forge.daemon._log_embedder_drift_warning"),
            patch("corpus_forge.daemon._get_any_backend") as mock_backend,
            patch("corpus_forge.daemon.time.sleep", side_effect=SystemExit(0)) as mock_sleep,
            patch("corpus_forge.daemon.logger"),
            pytest.raises(SystemExit),
        ):
            main()
        # 3600s long-sleep (no drift interval), and the checker never
        # touched the backend for shared config.
        mock_sleep.assert_called_once_with(3600.0)
        mock_backend.assert_not_called()

    def test_main_enabled_runs_checker_each_tick(self) -> None:
        """``main`` with federation on wakes on the interval and checks."""
        config = _make_config(
            federation=FederationConfig(enabled=True, drift_check_interval_s=120.0)
        )
        backend = MagicMock()
        backend.get_shared_config.return_value = (5, {"k": "v"})
        # Let the loop run two ticks then break out via SystemExit on the
        # third sleep.
        with (
            patch("corpus_forge.config.Config.load", return_value=config),
            patch("corpus_forge.logging_config.init_logging"),
            patch("corpus_forge.daemon.run_daemon"),
            patch("corpus_forge.daemon._log_embedder_drift_warning"),
            patch("corpus_forge.daemon._get_any_backend", return_value=backend),
            patch(
                "corpus_forge.admin.federation.read_last_pulled_version",
                return_value=1,
            ),
            patch(
                "corpus_forge.daemon.time.sleep",
                side_effect=[None, None, SystemExit(0)],
            ) as mock_sleep,
            patch("corpus_forge.daemon.logger") as mock_logger,
            pytest.raises(SystemExit),
        ):
            main()
        # Woke on the 120s drift interval, not the 3600s default.
        assert mock_sleep.call_args_list[0][0][0] == 120.0
        # Drift fired (published 5 > pulled 1).
        assert mock_logger.warning.called


class TestDriftWarning:
    """The WARN-on-drift behaviour and its negative cases."""

    def _run_once(self, *, published: int | None, last_pulled: int):
        config = _make_config(
            federation=FederationConfig(enabled=True, drift_check_interval_s=300.0)
        )
        checker = _make_federation_drift_checker(config)
        assert checker is not None

        backend = MagicMock()
        backend.get_shared_config.return_value = (
            None if published is None else (published, {"k": "v"})
        )
        with (
            patch("corpus_forge.daemon._get_any_backend", return_value=backend),
            patch(
                "corpus_forge.admin.federation.read_last_pulled_version",
                return_value=last_pulled,
            ),
            patch("corpus_forge.daemon.logger") as mock_logger,
        ):
            checker()
        return mock_logger, backend

    def test_warns_when_published_ahead(self) -> None:
        mock_logger, backend = self._run_once(published=3, last_pulled=1)
        backend.get_shared_config.assert_called_once()
        mock_logger.warning.assert_called_once()
        msg = mock_logger.warning.call_args[0][0]
        assert "config pull" in msg

    def test_no_warn_when_equal(self) -> None:
        mock_logger, _ = self._run_once(published=2, last_pulled=2)
        mock_logger.warning.assert_not_called()

    def test_no_warn_when_local_ahead(self) -> None:
        mock_logger, _ = self._run_once(published=1, last_pulled=4)
        mock_logger.warning.assert_not_called()

    def test_no_warn_when_nothing_published(self) -> None:
        mock_logger, backend = self._run_once(published=None, last_pulled=0)
        backend.get_shared_config.assert_called_once()
        mock_logger.warning.assert_not_called()


class TestThrottle:
    """Two wakeups inside one interval ⇒ one backend read."""

    def test_throttle_within_interval(self) -> None:
        config = _make_config(
            federation=FederationConfig(enabled=True, drift_check_interval_s=300.0)
        )
        checker = _make_federation_drift_checker(config)
        assert checker is not None

        backend = MagicMock()
        backend.get_shared_config.return_value = (1, {"k": "v"})
        # Two monotonic readings 10s apart — well inside the 300s window.
        with (
            patch("corpus_forge.daemon._get_any_backend", return_value=backend),
            patch(
                "corpus_forge.admin.federation.read_last_pulled_version",
                return_value=1,
            ),
            patch("corpus_forge.daemon.time.monotonic", side_effect=[100.0, 110.0]),
        ):
            checker()
            checker()
        # First tick checks; second is throttled.
        assert backend.get_shared_config.call_count == 1

    def test_checks_again_after_interval(self) -> None:
        config = _make_config(
            federation=FederationConfig(enabled=True, drift_check_interval_s=300.0)
        )
        checker = _make_federation_drift_checker(config)
        assert checker is not None

        backend = MagicMock()
        backend.get_shared_config.return_value = (1, {"k": "v"})
        # Two readings 400s apart — the second clears the 300s window.
        with (
            patch("corpus_forge.daemon._get_any_backend", return_value=backend),
            patch(
                "corpus_forge.admin.federation.read_last_pulled_version",
                return_value=1,
            ),
            patch("corpus_forge.daemon.time.monotonic", side_effect=[100.0, 500.0]),
        ):
            checker()
            checker()
        assert backend.get_shared_config.call_count == 2


class TestFailureIsolation:
    """Every failure mode is silent — no warning, no crash."""

    def test_unreachable_backend_is_silent(self) -> None:
        config = _make_config(federation=FederationConfig(enabled=True))
        checker = _make_federation_drift_checker(config)
        assert checker is not None
        with (
            patch("corpus_forge.daemon._get_any_backend", return_value=None),
            patch("corpus_forge.daemon.logger") as mock_logger,
        ):
            checker()  # must not raise
        mock_logger.warning.assert_not_called()

    def test_get_shared_config_raising_is_silent(self) -> None:
        config = _make_config(federation=FederationConfig(enabled=True))
        checker = _make_federation_drift_checker(config)
        assert checker is not None

        backend = MagicMock()
        backend.get_shared_config.side_effect = RuntimeError("conn reset")
        with (
            patch("corpus_forge.daemon._get_any_backend", return_value=backend),
            patch("corpus_forge.daemon.logger") as mock_logger,
        ):
            checker()  # must not raise
        mock_logger.warning.assert_not_called()

    def test_federation_unsupported_is_silent(self) -> None:
        config = _make_config(federation=FederationConfig(enabled=True))
        checker = _make_federation_drift_checker(config)
        assert checker is not None

        backend = MagicMock()
        backend.get_shared_config.side_effect = FederationUnsupported("sqlite")
        with (
            patch("corpus_forge.daemon._get_any_backend", return_value=backend),
            patch("corpus_forge.daemon.logger") as mock_logger,
        ):
            checker()  # must not raise
        mock_logger.warning.assert_not_called()

    def test_get_any_backend_raising_is_silent(self) -> None:
        config = _make_config(federation=FederationConfig(enabled=True))
        checker = _make_federation_drift_checker(config)
        assert checker is not None
        with (
            patch(
                "corpus_forge.daemon._get_any_backend",
                side_effect=RuntimeError("boom"),
            ),
            patch("corpus_forge.daemon.logger") as mock_logger,
        ):
            checker()  # must not raise
        mock_logger.warning.assert_not_called()
