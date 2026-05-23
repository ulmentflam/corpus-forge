"""Regression tests for the ``embedder_drift`` doctor check.

Background
----------
The maintainer's instance hit silent embedder drift on 2026-05-22:
renaming ``qwen3-2000`` → ``qwen3-4096`` in ``config.toml`` left the
original ``corpus.embedders`` row + 209 MB ``embeddings_qwen3_2000``
table behind in the DB, and ``corpus-forge embedder list`` (which
only reads config) was blind to it. ``embedder_drift`` is the
doctor check that surfaces this case; this module pins its contract.

Mocking strategy
----------------
Same shape as :mod:`tests.unit.test_doctor_embedder_indexes`: a
:class:`MagicMock` stands in for the ``PostgresBackend`` so the
check's branching can be exercised without a real Postgres server.
The audit helper (``audit_embedder_drift``) is also mocked at the
import site so we lock the doctor-side branching independent of the
audit logic (which has its own unit tests in
:mod:`tests.unit.test_embedder_gc`).

What we pin
-----------
1. SQLite backend → ``SKIP``.
2. Postgres unreachable / import error → ``SKIP``.
3. Audit raises (e.g. pre-migrate, schema missing) → ``SKIP``.
4. No orphans → ``OK``.
5. One or more orphans → ``WARN`` with the ``embedder gc --apply``
   recovery hint AND a reclaimable-bytes total derived from the
   orphan rows' ``table_size_bytes``.
6. ``embedder_drift`` is registered in ``run_doctor`` so it always
   shows up in ``corpus-forge doctor`` output even when the config
   has zero embedders.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from corpus_forge.admin.embedder import EmbedderDriftRow
from corpus_forge.doctor.checks import (
    CheckStatus,
    _check_embedder_drift,
    run_doctor,
)


def _cfg(kind: str = "postgres") -> MagicMock:
    cfg = MagicMock()
    cfg.backend.kind = kind
    cfg.backend.dsn = "postgresql://x:y@localhost/z"
    cfg.backend.schema = "corpus"
    cfg.embedders = []
    return cfg


class TestSkipBranches:
    """SKIP results: the check stays out of the way when it can't act."""

    def test_sqlite_backend_skips_immediately(self) -> None:
        result = _check_embedder_drift(_cfg("sqlite"))
        assert result.status is CheckStatus.SKIP
        assert "sqlite" in result.detail.lower()

    def test_postgres_unreachable_yields_skip(self) -> None:
        """Doctor must not wedge if Postgres is temporarily down."""

        with patch(
            "corpus_forge.backends.postgres.PostgresBackend",
            side_effect=RuntimeError("connection refused"),
        ):
            result = _check_embedder_drift(_cfg())
        assert result.status is CheckStatus.SKIP
        assert "unreachable" in result.detail

    def test_pre_migrate_state_yields_skip(self) -> None:
        """``corpus.embedders`` missing entirely → audit raises → SKIP."""

        fake_backend = MagicMock()
        with (
            patch(
                "corpus_forge.backends.postgres.PostgresBackend",
                return_value=fake_backend,
            ),
            patch(
                "corpus_forge.admin.embedder.audit_embedder_drift",
                side_effect=RuntimeError("relation corpus.embedders does not exist"),
            ),
        ):
            result = _check_embedder_drift(_cfg())
        assert result.status is CheckStatus.SKIP
        assert "audit failed" in result.detail.lower()


class TestOkBranch:
    """OK: every DB-side embedder name exists in the config."""

    def test_returns_ok_when_no_orphans(self) -> None:
        fake_backend = MagicMock()
        with (
            patch(
                "corpus_forge.backends.postgres.PostgresBackend",
                return_value=fake_backend,
            ),
            patch(
                "corpus_forge.admin.embedder.audit_embedder_drift",
                return_value=[],
            ),
        ):
            result = _check_embedder_drift(_cfg())
        assert result.status is CheckStatus.OK
        assert "match" in result.detail.lower()


class TestWarnBranch:
    """WARN: one or more orphan rows exist; recovery hint is included."""

    def test_single_orphan_warns(self) -> None:
        """The original maintainer-instance bug shape: one orphan, ~209 MB."""

        orphan = EmbedderDriftRow(
            name="qwen3-2000",
            db_id=2,
            dimension=2000,
            table_name="embeddings_qwen3_2000",
            table_exists=True,
            table_size_bytes=219_103_232,  # ≈209 MB
            row_count=8976,
        )
        fake_backend = MagicMock()
        with (
            patch(
                "corpus_forge.backends.postgres.PostgresBackend",
                return_value=fake_backend,
            ),
            patch(
                "corpus_forge.admin.embedder.audit_embedder_drift",
                return_value=[orphan],
            ),
        ):
            result = _check_embedder_drift(_cfg())
        assert result.status is CheckStatus.WARN
        assert "qwen3-2000" in result.detail
        assert "embedder gc --apply" in result.detail
        # The detail should also surface the reclaimable size.
        assert "MB" in result.detail
        assert "8976" in result.detail

    def test_multiple_orphans_warn_aggregates_size(self) -> None:
        orphans = [
            EmbedderDriftRow(
                name="qwen3-2000",
                db_id=2,
                dimension=2000,
                table_name="embeddings_qwen3_2000",
                table_exists=True,
                table_size_bytes=104_857_600,  # 100 MB
                row_count=4000,
            ),
            EmbedderDriftRow(
                name="bge-large",
                db_id=3,
                dimension=1024,
                table_name="embeddings_bge_large",
                table_exists=True,
                table_size_bytes=52_428_800,  # 50 MB
                row_count=2000,
            ),
        ]
        fake_backend = MagicMock()
        with (
            patch(
                "corpus_forge.backends.postgres.PostgresBackend",
                return_value=fake_backend,
            ),
            patch(
                "corpus_forge.admin.embedder.audit_embedder_drift",
                return_value=orphans,
            ),
        ):
            result = _check_embedder_drift(_cfg())
        assert result.status is CheckStatus.WARN
        assert "qwen3-2000" in result.detail
        assert "bge-large" in result.detail
        # 100 MB + 50 MB = 150 MB
        assert "150" in result.detail

    def test_warn_tolerates_missing_size_and_row_count(self) -> None:
        """A partially-cleaned orphan (table dropped, row remained) still WARNs."""

        orphan = EmbedderDriftRow(
            name="ghost",
            db_id=7,
            dimension=768,
            table_name="embeddings_ghost",
            table_exists=False,
            table_size_bytes=None,
            row_count=None,
        )
        fake_backend = MagicMock()
        with (
            patch(
                "corpus_forge.backends.postgres.PostgresBackend",
                return_value=fake_backend,
            ),
            patch(
                "corpus_forge.admin.embedder.audit_embedder_drift",
                return_value=[orphan],
            ),
        ):
            result = _check_embedder_drift(_cfg())
        assert result.status is CheckStatus.WARN
        assert "ghost" in result.detail


class TestRegisteredInRunDoctor:
    """``run_doctor`` must include the new check unconditionally."""

    def test_embedder_drift_appears_in_report_names(self, tmp_path) -> None:
        """When the config can't be loaded, the check still appears as SKIP."""

        report = run_doctor(config_path=tmp_path / "no-config.toml")
        names = {r.name for r in report.results}
        assert "embedder_drift" in names

    def test_embedder_drift_skipped_when_config_missing(self, tmp_path) -> None:
        """Same as above but pin the SKIP shape so the wiring stays honest."""

        report = run_doctor(config_path=tmp_path / "no-config.toml")
        rows = [r for r in report.results if r.name == "embedder_drift"]
        assert len(rows) == 1
        assert rows[0].status is CheckStatus.SKIP
        assert "config not loaded" in rows[0].detail
