"""Tests for the informational ``model_telemetry`` doctor check (rfc-fleet-1).

The check NEVER blocks doctor — every outcome is ``OK`` (or ``SKIP`` when
config didn't load).  What we pin:

1. Rows present → ``OK`` "N benchmark row(s), freshest <age> ago".
2. Empty table → ``OK`` with the ``bench embed --all`` calibration hint
   (NOT a warning — passive telemetry fills it on the first real embed).
3. Backend build fails / unreachable → ``OK`` "telemetry unavailable".
4. ``model_benchmark_stats`` raises (pre-migrate) → ``OK`` "telemetry
   unavailable".
5. The check is registered in ``run_doctor`` and is informational there.

The backend is mocked so no real DB is needed; ``CheckStatus`` is never
``WARN``/``FAIL`` on any branch — that's the load-bearing invariant.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from corpus_forge.doctor.checks import (
    CheckStatus,
    _check_model_telemetry,
    run_doctor,
)


def _cfg(kind: str = "postgres") -> MagicMock:
    cfg = MagicMock()
    cfg.backend.kind = kind
    cfg.backend.dsn = "postgresql://x:y@localhost/z"
    cfg.backend.schema = "corpus"
    return cfg


class TestRowsPresent:
    def test_postgres_rows_present_ok_with_age(self) -> None:
        backend = MagicMock()
        backend.model_benchmark_stats.return_value = {
            "count": 7,
            "freshest": datetime.now(tz=UTC) - timedelta(hours=2),
        }
        with patch("corpus_forge.backends.postgres.PostgresBackend", return_value=backend):
            result = _check_model_telemetry(_cfg("postgres"))
        assert result.status == CheckStatus.OK
        assert "7 benchmark row(s)" in result.detail
        assert "ago" in result.detail

    def test_sqlite_rows_present_ok(self) -> None:
        backend = MagicMock()
        backend.model_benchmark_stats.return_value = {
            "count": 1,
            "freshest": datetime.now(tz=UTC),
        }
        with patch("corpus_forge.backends.sqlite.SQLiteBackend", return_value=backend):
            result = _check_model_telemetry(_cfg("sqlite"))
        assert result.status == CheckStatus.OK
        assert "1 benchmark row(s)" in result.detail


class TestEmptyTable:
    def test_empty_table_ok_with_hint(self) -> None:
        backend = MagicMock()
        backend.model_benchmark_stats.return_value = {"count": 0, "freshest": None}
        with patch("corpus_forge.backends.postgres.PostgresBackend", return_value=backend):
            result = _check_model_telemetry(_cfg("postgres"))
        # Informational, NOT a warning.
        assert result.status == CheckStatus.OK
        assert "no benchmarks yet" in result.detail
        assert "bench embed --all" in result.detail


class TestUnavailable:
    def test_backend_construction_fails_ok(self) -> None:
        with patch(
            "corpus_forge.backends.postgres.PostgresBackend",
            side_effect=RuntimeError("backend down"),
        ):
            result = _check_model_telemetry(_cfg("postgres"))
        assert result.status == CheckStatus.OK
        assert "telemetry unavailable" in result.detail

    def test_stats_raises_pre_migrate_ok(self) -> None:
        backend = MagicMock()
        backend.model_benchmark_stats.side_effect = Exception("no such table")
        with patch("corpus_forge.backends.postgres.PostgresBackend", return_value=backend):
            result = _check_model_telemetry(_cfg("postgres"))
        assert result.status == CheckStatus.OK
        assert "telemetry unavailable" in result.detail


class TestNeverFails:
    """The single most important property: the check never blocks doctor."""

    def test_no_branch_warns_or_fails(self) -> None:
        # Build a stats result that, whatever the branch, must stay OK.
        backend = MagicMock()
        for stats in (
            {"count": 0, "freshest": None},
            {"count": 5, "freshest": datetime.now(tz=UTC)},
        ):
            backend.model_benchmark_stats.return_value = stats
            with patch("corpus_forge.backends.sqlite.SQLiteBackend", return_value=backend):
                result = _check_model_telemetry(_cfg("sqlite"))
            assert result.status not in (CheckStatus.WARN, CheckStatus.FAIL)


class TestRegisteredInRunDoctor:
    """``run_doctor`` must include the new check unconditionally."""

    def test_model_telemetry_appears_in_report_names(self, tmp_path) -> None:
        report = run_doctor(config_path=tmp_path / "no-config.toml")
        names = {r.name for r in report.results}
        assert "model_telemetry" in names

    def test_model_telemetry_skipped_when_config_missing(self, tmp_path) -> None:
        report = run_doctor(config_path=tmp_path / "no-config.toml")
        rows = [r for r in report.results if r.name == "model_telemetry"]
        assert len(rows) == 1
        assert rows[0].status is CheckStatus.SKIP
        assert "config not loaded" in rows[0].detail
