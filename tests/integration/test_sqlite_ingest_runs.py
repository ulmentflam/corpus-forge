"""Integration tests for SQLiteBackend ingest-run CRUD methods — SR-T3.

Behavioral parity with SR-T2 (Postgres) but against SQLiteBackend in-process.
No Docker required; all tests use tmp_path fixtures.

Also covers the cross-process advisory file-lock helper
``corpus_forge.scanner.filelock`` which SQLite-backend installs use instead
of pg_advisory_lock.

RED condition
-------------
- ``SQLiteBackend`` has no ``start_ingest_run``, ``update_ingest_run``,
  ``finish_ingest_run``, ``latest_ingest_run``, ``latest_unfinished_ingest_run``,
  ``upsert_ingest_run_source``, or ``find_source_last_scanned_at`` methods.
- ``corpus_forge.scanner.filelock`` module does not exist.
- ``corpus_forge.backends.base.IngestRunInProgressError`` does not exist.

Every test in this file should fail with AttributeError or ImportError at
collection/call time.
"""

from __future__ import annotations

import socket
import sqlite3
import subprocess
import sys
import textwrap
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from corpus_forge.backends.sqlite import SQLiteBackend

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_backend(db_path: Path) -> SQLiteBackend:
    """Create a migrated SQLiteBackend at *db_path*."""
    backend = SQLiteBackend(path=str(db_path))
    backend.migrate()
    return backend


def _raw(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _run_id(suffix: str = "a") -> str:
    return f"test-run-{suffix}-{int(time.time() * 1000)}"


def _dataset_id(backend: SQLiteBackend) -> int:
    return backend.get_or_create_dataset(
        name="sr_t3_test_ds", kind="text", description="SR-T3 test dataset"
    )


_HOST = socket.gethostname()
_PID = 42
_DIGEST = "deadbeefdeadbeef" * 4


# ---------------------------------------------------------------------------
# TestStartIngestRun
# ---------------------------------------------------------------------------


class TestStartIngestRun:
    """start_ingest_run inserts a row and is idempotent (resume path)."""

    def test_happy_path_inserts_row(self, tmp_path):
        """start_ingest_run inserts an ingest_runs row with status='running'."""
        backend = _make_backend(tmp_path / "db.sqlite")
        run_id = _run_id("start-happy")
        backend.start_ingest_run(
            run_id=run_id,
            host=_HOST,
            pid=_PID,
            config_digest=_DIGEST,
        )
        conn = _raw(tmp_path / "db.sqlite")
        try:
            row = conn.execute("SELECT * FROM ingest_runs WHERE run_id = ?", (run_id,)).fetchone()
        finally:
            conn.close()
        assert row is not None, "Expected a row in ingest_runs after start_ingest_run"
        assert row["status"] == "running"
        assert row["host"] == _HOST
        assert row["pid"] == _PID
        assert row["config_digest"] == _DIGEST
        assert row["ended_at"] is None
        assert row["last_done"] == 0

    def test_resume_flips_status_to_running(self, tmp_path):
        """Calling start_ingest_run a second time with same run_id flips status to running
        and clears ended_at but leaves started_at unchanged."""
        backend = _make_backend(tmp_path / "db.sqlite")
        run_id = _run_id("start-resume")
        backend.start_ingest_run(run_id=run_id, host=_HOST, pid=_PID, config_digest=_DIGEST)
        # Manually finish the run so ended_at is set
        backend.finish_ingest_run(run_id, status="interrupted")

        conn = _raw(tmp_path / "db.sqlite")
        try:
            row_before = conn.execute(
                "SELECT started_at FROM ingest_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            started_at_before = row_before["started_at"]
        finally:
            conn.close()

        # Now resume — start_ingest_run should flip back to 'running'
        backend.start_ingest_run(run_id=run_id, host=_HOST, pid=_PID, config_digest=_DIGEST)

        conn = _raw(tmp_path / "db.sqlite")
        try:
            row = conn.execute("SELECT * FROM ingest_runs WHERE run_id = ?", (run_id,)).fetchone()
        finally:
            conn.close()

        assert row["status"] == "running"
        assert row["ended_at"] is None, "ended_at must be cleared on resume"
        assert row["started_at"] == started_at_before, "started_at must not change on resume"

    def test_no_duplicate_rows_on_resume(self, tmp_path):
        """Resuming must not create a second row."""
        backend = _make_backend(tmp_path / "db.sqlite")
        run_id = _run_id("start-no-dup")
        backend.start_ingest_run(run_id=run_id, host=_HOST, pid=_PID, config_digest=_DIGEST)
        backend.finish_ingest_run(run_id, status="interrupted")
        backend.start_ingest_run(run_id=run_id, host=_HOST, pid=_PID, config_digest=_DIGEST)

        conn = _raw(tmp_path / "db.sqlite")
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM ingest_runs WHERE run_id = ?", (run_id,)
            ).fetchone()[0]
        finally:
            conn.close()
        assert count == 1, f"Expected exactly 1 row after resume, got {count}"

    def test_missing_run_id_is_required(self, tmp_path):
        """start_ingest_run with empty run_id must raise (or produce an error row,
        but not silently succeed without a run_id)."""
        backend = _make_backend(tmp_path / "db.sqlite")
        with pytest.raises(Exception):
            backend.start_ingest_run(
                run_id="",
                host=_HOST,
                pid=_PID,
                config_digest=_DIGEST,
            )


# ---------------------------------------------------------------------------
# TestUpdateIngestRun
# ---------------------------------------------------------------------------


class TestUpdateIngestRun:
    """update_ingest_run writes heartbeat columns and swallows errors silently."""

    def test_updates_last_op_and_last_done(self, tmp_path):
        """update_ingest_run sets last_op and last_done correctly."""
        backend = _make_backend(tmp_path / "db.sqlite")
        run_id = _run_id("upd-happy")
        backend.start_ingest_run(run_id=run_id, host=_HOST, pid=_PID, config_digest=_DIGEST)
        backend.update_ingest_run(run_id, last_op="scan", last_done=5, last_total=20)

        conn = _raw(tmp_path / "db.sqlite")
        try:
            row = conn.execute(
                "SELECT last_op, last_done, last_total FROM ingest_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        finally:
            conn.close()
        assert row["last_op"] == "scan"
        assert row["last_done"] == 5
        assert row["last_total"] == 20

    def test_partial_update_only_sets_supplied_fields(self, tmp_path):
        """update_ingest_run with only last_done must leave last_op unchanged."""
        backend = _make_backend(tmp_path / "db.sqlite")
        run_id = _run_id("upd-partial")
        backend.start_ingest_run(run_id=run_id, host=_HOST, pid=_PID, config_digest=_DIGEST)
        backend.update_ingest_run(run_id, last_op="chunk")
        backend.update_ingest_run(run_id, last_done=10)

        conn = _raw(tmp_path / "db.sqlite")
        try:
            row = conn.execute(
                "SELECT last_op, last_done FROM ingest_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        finally:
            conn.close()
        assert row["last_op"] == "chunk"
        assert row["last_done"] == 10

    def test_swallows_operational_error_and_logs_debug(self, tmp_path, caplog):
        """update_ingest_run MUST NOT propagate OperationalError; MUST log at DEBUG."""
        import logging

        backend = _make_backend(tmp_path / "db.sqlite")
        run_id = _run_id("upd-swallow")
        backend.start_ingest_run(run_id=run_id, host=_HOST, pid=_PID, config_digest=_DIGEST)

        # Poison the connection so the UPDATE will fail
        original_execute = backend._execute

        def broken_execute(sql, *args, **kwargs):
            if "UPDATE" in sql.upper() and "ingest_runs" in sql:
                raise sqlite3.OperationalError("simulated disk error")
            return original_execute(sql, *args, **kwargs)

        backend._execute = broken_execute  # type: ignore[method-assign]

        with caplog.at_level(logging.DEBUG):
            # Must not raise
            result = backend.update_ingest_run(run_id, last_op="embed_flush")

        assert result is None, "update_ingest_run must return None on OperationalError"
        debug_messages = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
        assert any(
            "checkpoint" in m.lower() or "ingest" in m.lower() or "failed" in m.lower()
            for m in debug_messages
        ), f"Expected a DEBUG log about the failure, got: {debug_messages}"

    def test_update_nonexistent_run_id_is_noop(self, tmp_path):
        """update_ingest_run on a non-existent run_id must not raise."""
        backend = _make_backend(tmp_path / "db.sqlite")
        backend.update_ingest_run("nonexistent-run-id", last_op="scan", last_done=99)


# ---------------------------------------------------------------------------
# TestFinishIngestRun
# ---------------------------------------------------------------------------


class TestFinishIngestRun:
    """finish_ingest_run status transitions and error column."""

    def test_completed_sets_ended_at(self, tmp_path):
        """finish_ingest_run('completed') sets ended_at to a non-NULL timestamp."""
        backend = _make_backend(tmp_path / "db.sqlite")
        run_id = _run_id("fin-completed")
        backend.start_ingest_run(run_id=run_id, host=_HOST, pid=_PID, config_digest=_DIGEST)
        backend.finish_ingest_run(run_id, status="completed")

        conn = _raw(tmp_path / "db.sqlite")
        try:
            row = conn.execute(
                "SELECT status, ended_at, error FROM ingest_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        finally:
            conn.close()
        assert row["status"] == "completed"
        assert row["ended_at"] is not None, "ended_at must be set on completion"
        assert row["error"] is None

    def test_interrupted_sets_status(self, tmp_path):
        """finish_ingest_run('interrupted') sets status correctly."""
        backend = _make_backend(tmp_path / "db.sqlite")
        run_id = _run_id("fin-interrupted")
        backend.start_ingest_run(run_id=run_id, host=_HOST, pid=_PID, config_digest=_DIGEST)
        backend.finish_ingest_run(run_id, status="interrupted")

        conn = _raw(tmp_path / "db.sqlite")
        try:
            row = conn.execute(
                "SELECT status FROM ingest_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        finally:
            conn.close()
        assert row["status"] == "interrupted"

    def test_failed_sets_error_column(self, tmp_path):
        """finish_ingest_run('failed', error='...') populates the error column."""
        backend = _make_backend(tmp_path / "db.sqlite")
        run_id = _run_id("fin-failed")
        backend.start_ingest_run(run_id=run_id, host=_HOST, pid=_PID, config_digest=_DIGEST)
        err_msg = "Traceback: OutOfMemoryError at line 42"
        backend.finish_ingest_run(run_id, status="failed", error=err_msg)

        conn = _raw(tmp_path / "db.sqlite")
        try:
            row = conn.execute(
                "SELECT status, error FROM ingest_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        finally:
            conn.close()
        assert row["status"] == "failed"
        assert row["error"] == err_msg

    def test_invalid_status_raises(self, tmp_path):
        """finish_ingest_run with invalid status must raise ValueError."""
        backend = _make_backend(tmp_path / "db.sqlite")
        run_id = _run_id("fin-invalid")
        backend.start_ingest_run(run_id=run_id, host=_HOST, pid=_PID, config_digest=_DIGEST)
        with pytest.raises((ValueError, Exception)):
            backend.finish_ingest_run(run_id, status="unknown_status")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# TestLatestIngestRun
# ---------------------------------------------------------------------------


class TestLatestIngestRun:
    """latest_ingest_run ordering and no-runs case."""

    def test_returns_none_when_no_runs(self, tmp_path):
        """latest_ingest_run returns None when the table is empty."""
        backend = _make_backend(tmp_path / "db.sqlite")
        result = backend.latest_ingest_run()
        assert result is None

    def test_returns_most_recent_by_started_at(self, tmp_path):
        """latest_ingest_run returns the row with the highest started_at."""
        backend = _make_backend(tmp_path / "db.sqlite")
        for suffix in ["x1", "x2", "x3"]:
            run_id = f"latestrun-{suffix}"
            backend.start_ingest_run(run_id=run_id, host=_HOST, pid=_PID, config_digest=_DIGEST)
            time.sleep(0.01)  # ensure distinct timestamps

        result = backend.latest_ingest_run()
        assert result is not None
        assert result["run_id"] == "latestrun-x3"

    def test_returns_dict_with_required_keys(self, tmp_path):
        """latest_ingest_run returns a dict with expected fields."""
        backend = _make_backend(tmp_path / "db.sqlite")
        run_id = _run_id("latest-keys")
        backend.start_ingest_run(run_id=run_id, host=_HOST, pid=_PID, config_digest=_DIGEST)
        row = backend.latest_ingest_run()
        assert row is not None
        for field in (
            "run_id",
            "status",
            "host",
            "pid",
            "started_at",
            "ended_at",
            "last_op",
            "last_done",
            "last_total",
            "error",
            "config_digest",
        ):
            assert field in row, f"Expected field '{field}' in latest_ingest_run result"

    def test_returns_latest_regardless_of_status(self, tmp_path):
        """latest_ingest_run returns the newest row even if it's completed."""
        backend = _make_backend(tmp_path / "db.sqlite")
        run_id_old = _run_id("old-completed")
        run_id_new = _run_id("new-completed")
        backend.start_ingest_run(run_id=run_id_old, host=_HOST, pid=_PID, config_digest=_DIGEST)
        backend.finish_ingest_run(run_id_old, status="completed")
        time.sleep(0.01)
        backend.start_ingest_run(run_id=run_id_new, host=_HOST, pid=_PID, config_digest=_DIGEST)
        backend.finish_ingest_run(run_id_new, status="completed")

        result = backend.latest_ingest_run()
        assert result is not None
        assert result["run_id"] == run_id_new


# ---------------------------------------------------------------------------
# TestLatestUnfinishedIngestRun
# ---------------------------------------------------------------------------


class TestLatestUnfinishedIngestRun:
    """latest_unfinished_ingest_run filter logic."""

    def test_returns_none_when_only_completed(self, tmp_path):
        """Returns None when the only run is completed."""
        backend = _make_backend(tmp_path / "db.sqlite")
        run_id = _run_id("uf-completed")
        backend.start_ingest_run(run_id=run_id, host=_HOST, pid=_PID, config_digest=_DIGEST)
        backend.finish_ingest_run(run_id, status="completed")
        result = backend.latest_unfinished_ingest_run()
        assert result is None

    def test_returns_none_when_only_failed(self, tmp_path):
        """Returns None when the only run is failed (failed is 'finished')."""
        backend = _make_backend(tmp_path / "db.sqlite")
        run_id = _run_id("uf-failed")
        backend.start_ingest_run(run_id=run_id, host=_HOST, pid=_PID, config_digest=_DIGEST)
        backend.finish_ingest_run(run_id, status="failed", error="boom")
        result = backend.latest_unfinished_ingest_run()
        assert result is None

    def test_returns_running_run(self, tmp_path):
        """Returns the running row."""
        backend = _make_backend(tmp_path / "db.sqlite")
        run_id = _run_id("uf-running")
        backend.start_ingest_run(run_id=run_id, host=_HOST, pid=_PID, config_digest=_DIGEST)
        result = backend.latest_unfinished_ingest_run()
        assert result is not None
        assert result["run_id"] == run_id
        assert result["status"] == "running"

    def test_returns_interrupted_run(self, tmp_path):
        """Returns the interrupted row (the canonical resumable state)."""
        backend = _make_backend(tmp_path / "db.sqlite")
        run_id = _run_id("uf-interrupted")
        backend.start_ingest_run(run_id=run_id, host=_HOST, pid=_PID, config_digest=_DIGEST)
        backend.finish_ingest_run(run_id, status="interrupted")
        result = backend.latest_unfinished_ingest_run()
        assert result is not None
        assert result["run_id"] == run_id

    def test_returns_none_when_table_empty(self, tmp_path):
        """Returns None when the ingest_runs table is empty."""
        backend = _make_backend(tmp_path / "db.sqlite")
        result = backend.latest_unfinished_ingest_run()
        assert result is None

    def test_prefers_most_recent_unfinished_over_older(self, tmp_path):
        """When two interrupted runs exist, returns the newer one."""
        backend = _make_backend(tmp_path / "db.sqlite")
        run_old = _run_id("uf-old")
        run_new = _run_id("uf-new")
        backend.start_ingest_run(run_id=run_old, host=_HOST, pid=_PID, config_digest=_DIGEST)
        backend.finish_ingest_run(run_old, status="interrupted")
        time.sleep(0.015)
        backend.start_ingest_run(run_id=run_new, host=_HOST, pid=_PID, config_digest=_DIGEST)
        backend.finish_ingest_run(run_new, status="interrupted")
        result = backend.latest_unfinished_ingest_run()
        assert result is not None
        assert result["run_id"] == run_new


# ---------------------------------------------------------------------------
# TestUpsertIngestRunSource
# ---------------------------------------------------------------------------


class TestUpsertIngestRunSource:
    """upsert_ingest_run_source: delta accumulation, finished_at, and ordering."""

    def test_creates_row_on_first_call(self, tmp_path):
        """upsert_ingest_run_source creates a new row on first call."""
        backend = _make_backend(tmp_path / "db.sqlite")
        ds_id = _dataset_id(backend)
        run_id = _run_id("urs-create")
        backend.start_ingest_run(run_id=run_id, host=_HOST, pid=_PID, config_digest=_DIGEST)
        backend.upsert_ingest_run_source(
            run_id=run_id,
            source_uri_prefix="fs:///vault",
            dataset_id=ds_id,
            docs_seen_delta=3,
        )
        conn = _raw(tmp_path / "db.sqlite")
        try:
            row = conn.execute(
                "SELECT * FROM ingest_run_sources WHERE run_id = ? AND source_uri_prefix = ?",
                (run_id, "fs:///vault"),
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        assert row["docs_seen"] == 3

    def test_deltas_accumulate_across_calls(self, tmp_path):
        """Calling upsert_ingest_run_source three times with docs_seen_delta=1 gives 3."""
        backend = _make_backend(tmp_path / "db.sqlite")
        ds_id = _dataset_id(backend)
        run_id = _run_id("urs-deltas")
        backend.start_ingest_run(run_id=run_id, host=_HOST, pid=_PID, config_digest=_DIGEST)
        prefix = "fs:///notes"
        for _ in range(3):
            backend.upsert_ingest_run_source(
                run_id=run_id,
                source_uri_prefix=prefix,
                dataset_id=ds_id,
                docs_seen_delta=1,
                docs_skipped_delta=0,
                docs_failed_delta=0,
            )
        conn = _raw(tmp_path / "db.sqlite")
        try:
            row = conn.execute(
                "SELECT docs_seen, docs_skipped, docs_failed FROM ingest_run_sources"
                " WHERE run_id = ? AND source_uri_prefix = ?",
                (run_id, prefix),
            ).fetchone()
        finally:
            conn.close()
        assert row["docs_seen"] == 3

    def test_skipped_and_failed_deltas_accumulate(self, tmp_path):
        """docs_skipped and docs_failed also accumulate correctly."""
        backend = _make_backend(tmp_path / "db.sqlite")
        ds_id = _dataset_id(backend)
        run_id = _run_id("urs-sfail")
        backend.start_ingest_run(run_id=run_id, host=_HOST, pid=_PID, config_digest=_DIGEST)
        prefix = "fs:///code"
        backend.upsert_ingest_run_source(
            run_id=run_id,
            source_uri_prefix=prefix,
            dataset_id=ds_id,
            docs_seen_delta=5,
            docs_skipped_delta=2,
            docs_failed_delta=1,
        )
        backend.upsert_ingest_run_source(
            run_id=run_id,
            source_uri_prefix=prefix,
            dataset_id=ds_id,
            docs_seen_delta=3,
            docs_skipped_delta=1,
            docs_failed_delta=0,
        )
        conn = _raw(tmp_path / "db.sqlite")
        try:
            row = conn.execute(
                "SELECT docs_seen, docs_skipped, docs_failed FROM ingest_run_sources"
                " WHERE run_id = ? AND source_uri_prefix = ?",
                (run_id, prefix),
            ).fetchone()
        finally:
            conn.close()
        assert row["docs_seen"] == 8
        assert row["docs_skipped"] == 3
        assert row["docs_failed"] == 1

    def test_finished_true_sets_finished_at(self, tmp_path):
        """finished=True sets finished_at to a non-NULL timestamp."""
        backend = _make_backend(tmp_path / "db.sqlite")
        ds_id = _dataset_id(backend)
        run_id = _run_id("urs-finish")
        backend.start_ingest_run(run_id=run_id, host=_HOST, pid=_PID, config_digest=_DIGEST)
        prefix = "fs:///archive"
        backend.upsert_ingest_run_source(
            run_id=run_id,
            source_uri_prefix=prefix,
            dataset_id=ds_id,
            docs_seen_delta=10,
            finished=True,
        )
        conn = _raw(tmp_path / "db.sqlite")
        try:
            row = conn.execute(
                "SELECT finished_at FROM ingest_run_sources"
                " WHERE run_id = ? AND source_uri_prefix = ?",
                (run_id, prefix),
            ).fetchone()
        finally:
            conn.close()
        assert row["finished_at"] is not None, "finished_at must be set when finished=True"

    def test_finished_false_leaves_finished_at_null(self, tmp_path):
        """finished=False (default) leaves finished_at NULL."""
        backend = _make_backend(tmp_path / "db.sqlite")
        ds_id = _dataset_id(backend)
        run_id = _run_id("urs-notfinish")
        backend.start_ingest_run(run_id=run_id, host=_HOST, pid=_PID, config_digest=_DIGEST)
        prefix = "fs:///inbox"
        backend.upsert_ingest_run_source(
            run_id=run_id,
            source_uri_prefix=prefix,
            dataset_id=ds_id,
            docs_seen_delta=2,
        )
        conn = _raw(tmp_path / "db.sqlite")
        try:
            row = conn.execute(
                "SELECT finished_at FROM ingest_run_sources"
                " WHERE run_id = ? AND source_uri_prefix = ?",
                (run_id, prefix),
            ).fetchone()
        finally:
            conn.close()
        assert row["finished_at"] is None

    def test_last_scanned_at_is_persisted(self, tmp_path):
        """last_scanned_at supplied to upsert_ingest_run_source is stored."""
        backend = _make_backend(tmp_path / "db.sqlite")
        ds_id = _dataset_id(backend)
        run_id = _run_id("urs-scanned")
        backend.start_ingest_run(run_id=run_id, host=_HOST, pid=_PID, config_digest=_DIGEST)
        prefix = "fs:///scanned"
        scanned = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
        backend.upsert_ingest_run_source(
            run_id=run_id,
            source_uri_prefix=prefix,
            dataset_id=ds_id,
            last_scanned_at=scanned,
        )
        conn = _raw(tmp_path / "db.sqlite")
        try:
            row = conn.execute(
                "SELECT last_scanned_at FROM ingest_run_sources"
                " WHERE run_id = ? AND source_uri_prefix = ?",
                (run_id, prefix),
            ).fetchone()
        finally:
            conn.close()
        assert row["last_scanned_at"] is not None
        # Accept ISO string or datetime — just assert non-null and contains the date
        assert "2026-01-15" in str(row["last_scanned_at"])

    def test_unique_run_id_source_prefix_constraint(self, tmp_path):
        """Two calls with the same (run_id, source_uri_prefix) must upsert, not double-insert."""
        backend = _make_backend(tmp_path / "db.sqlite")
        ds_id = _dataset_id(backend)
        run_id = _run_id("urs-unique")
        backend.start_ingest_run(run_id=run_id, host=_HOST, pid=_PID, config_digest=_DIGEST)
        prefix = "fs:///unique"
        backend.upsert_ingest_run_source(
            run_id=run_id,
            source_uri_prefix=prefix,
            dataset_id=ds_id,
            docs_seen_delta=1,
        )
        backend.upsert_ingest_run_source(
            run_id=run_id,
            source_uri_prefix=prefix,
            dataset_id=ds_id,
            docs_seen_delta=1,
        )
        conn = _raw(tmp_path / "db.sqlite")
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM ingest_run_sources"
                " WHERE run_id = ? AND source_uri_prefix = ?",
                (run_id, prefix),
            ).fetchone()[0]
        finally:
            conn.close()
        assert count == 1, f"Expected 1 row but got {count} (upsert must not double-insert)"


# ---------------------------------------------------------------------------
# TestFindSourceLastScannedAt
# ---------------------------------------------------------------------------


class TestFindSourceLastScannedAt:
    """find_source_last_scanned_at returns the max last_scanned_at across runs."""

    def test_returns_none_when_never_scanned(self, tmp_path):
        """Returns None when no ingest_run_sources rows exist for this prefix."""
        backend = _make_backend(tmp_path / "db.sqlite")
        result = backend.find_source_last_scanned_at("fs:///never")
        assert result is None

    def test_returns_latest_scanned_at_across_runs(self, tmp_path):
        """Returns the max(last_scanned_at) across all completed/interrupted runs."""
        backend = _make_backend(tmp_path / "db.sqlite")
        ds_id = _dataset_id(backend)
        prefix = "fs:///vault"

        # Run 1 — older scan
        run1 = _run_id("fsl-run1")
        backend.start_ingest_run(run_id=run1, host=_HOST, pid=_PID, config_digest=_DIGEST)
        old_ts = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
        backend.upsert_ingest_run_source(
            run_id=run1,
            source_uri_prefix=prefix,
            dataset_id=ds_id,
            last_scanned_at=old_ts,
            finished=True,
        )
        backend.finish_ingest_run(run1, status="completed")

        # Run 2 — newer scan
        run2 = _run_id("fsl-run2")
        backend.start_ingest_run(run_id=run2, host=_HOST, pid=_PID, config_digest=_DIGEST)
        new_ts = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
        backend.upsert_ingest_run_source(
            run_id=run2,
            source_uri_prefix=prefix,
            dataset_id=ds_id,
            last_scanned_at=new_ts,
            finished=True,
        )
        backend.finish_ingest_run(run2, status="completed")

        result = backend.find_source_last_scanned_at(prefix)
        assert result is not None
        # The returned value should be the newer timestamp (or close to it)
        result_str = str(result)
        assert "2026-05-01" in result_str or result >= new_ts, (
            f"Expected newest scanned_at, got {result}"
        )

    def test_different_prefix_returns_none(self, tmp_path):
        """A different source_uri_prefix returns None even if others were scanned."""
        backend = _make_backend(tmp_path / "db.sqlite")
        ds_id = _dataset_id(backend)
        run_id = _run_id("fsl-diff-prefix")
        backend.start_ingest_run(run_id=run_id, host=_HOST, pid=_PID, config_digest=_DIGEST)
        backend.upsert_ingest_run_source(
            run_id=run_id,
            source_uri_prefix="fs:///notes",
            dataset_id=ds_id,
            last_scanned_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        backend.finish_ingest_run(run_id, status="completed")

        result = backend.find_source_last_scanned_at("fs:///other_source")
        assert result is None

    def test_running_source_not_included_in_max(self, tmp_path):
        """A source from a still-running ingest is ignored by find_source_last_scanned_at
        (only completed/interrupted runs contribute)."""
        backend = _make_backend(tmp_path / "db.sqlite")
        ds_id = _dataset_id(backend)
        prefix = "fs:///active"

        # Old completed run
        run_old = _run_id("fsl-old")
        backend.start_ingest_run(run_id=run_old, host=_HOST, pid=_PID, config_digest=_DIGEST)
        old_ts = datetime(2026, 1, 1, tzinfo=UTC)
        backend.upsert_ingest_run_source(
            run_id=run_old,
            source_uri_prefix=prefix,
            dataset_id=ds_id,
            last_scanned_at=old_ts,
            finished=True,
        )
        backend.finish_ingest_run(run_old, status="completed")

        # New still-running run (not finished)
        run_new = _run_id("fsl-new-running")
        backend.start_ingest_run(run_id=run_new, host=_HOST, pid=_PID, config_digest=_DIGEST)
        new_ts = datetime(2026, 6, 1, tzinfo=UTC)
        backend.upsert_ingest_run_source(
            run_id=run_new,
            source_uri_prefix=prefix,
            dataset_id=ds_id,
            last_scanned_at=new_ts,
        )
        # Note: run_new is still 'running' — do NOT call finish_ingest_run

        result = backend.find_source_last_scanned_at(prefix)
        # Should return old_ts (from completed run), not new_ts (from still-running)
        assert result is not None
        result_str = str(result)
        assert "2026-01-01" in result_str or "2026-06" not in result_str, (
            f"Running run's last_scanned_at should not be returned; got {result}"
        )


# ---------------------------------------------------------------------------
# TestFileLock (tests for corpus_forge.scanner.filelock)
# ---------------------------------------------------------------------------


class TestFileLock:
    """Tests for the cross-process file-lock helper.

    Imports corpus_forge.scanner.filelock (module does not yet exist — ImportError
    is the expected RED state).
    """

    def test_import(self):
        """The filelock module must be importable."""
        from corpus_forge.scanner import filelock  # noqa: F401

    def test_acquire_returns_context_manager(self, tmp_path):
        """filelock.acquire(path) returns an object usable as a context manager."""
        from corpus_forge.scanner.filelock import acquire

        lock_path = tmp_path / "test.lock"
        ctx = acquire(lock_path)
        assert hasattr(ctx, "__enter__") and hasattr(ctx, "__exit__"), (
            "acquire() must return a context manager"
        )

    def test_acquire_returns_true_when_acquired(self, tmp_path):
        """filelock.acquire(path) yields True when the lock is successfully taken."""
        from corpus_forge.scanner.filelock import acquire

        lock_path = tmp_path / "test.lock"
        with acquire(lock_path) as held:
            assert held is True, f"Expected True (acquired), got {held!r}"

    def test_lock_released_after_context_exit(self, tmp_path):
        """The lock must be released after the context manager exits so a second
        acquisition in the same process succeeds."""
        from corpus_forge.scanner.filelock import acquire

        lock_path = tmp_path / "reacquire.lock"
        with acquire(lock_path):
            pass
        # Second acquisition in the same process should succeed
        with acquire(lock_path) as held:
            assert held is True, "Lock must be re-acquirable after previous context exits"

    def test_lock_released_even_on_exception(self, tmp_path):
        """The lock must be released when the context body raises an exception."""
        from corpus_forge.scanner.filelock import acquire

        lock_path = tmp_path / "exc.lock"
        try:
            with acquire(lock_path):
                raise RuntimeError("test exception inside lock")
        except RuntimeError:
            pass

        # Lock should now be free
        with acquire(lock_path) as held:
            assert held is True, "Lock must be released after exception in context body"

    def test_wait_false_returns_false_or_raises_on_contention(self, tmp_path):
        """With wait=False, a contended lock must return False or raise
        (not block).  Tested in-process with two threads."""
        from corpus_forge.scanner.filelock import acquire

        lock_path = tmp_path / "contend.lock"
        results: list[bool | Exception] = []
        barrier = threading.Barrier(2)

        def hold_lock():
            with acquire(lock_path, wait=True) as held:
                results.append(held)
                barrier.wait()  # signal that we hold the lock
                time.sleep(0.3)  # keep it held

        def try_lock():
            barrier.wait()  # wait until first thread holds the lock
            time.sleep(0.05)  # small buffer
            try:
                ctx = acquire(lock_path, wait=False)
                with ctx as held:
                    results.append(held)
            except Exception as exc:
                results.append(exc)

        t1 = threading.Thread(target=hold_lock)
        t2 = threading.Thread(target=try_lock)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert len(results) == 2, f"Expected 2 results, got: {results}"
        # First result must be True (lock holder)
        assert results[0] is True, f"Lock holder must get True, got {results[0]}"
        # Second result must be False or an exception (failed to acquire)
        second = results[1]
        assert second is False or isinstance(second, Exception), (
            f"Contending thread must get False or raise, got {second!r}"
        )

    def test_same_path_is_same_lock(self, tmp_path):
        """Two acquire() calls on the same path contend for the same lock."""
        from corpus_forge.scanner.filelock import acquire

        lock_path = tmp_path / "same.lock"
        # Just verify they're backed by the same file; contention tested above
        with acquire(lock_path):
            # Second acquire on same path with wait=False should fail
            try:
                ctx2 = acquire(lock_path, wait=False)
                with ctx2 as held2:
                    assert held2 is False, "Same path, second acquisition (no wait) must fail"
            except Exception:
                pass  # raising is also acceptable

    def test_different_paths_are_independent_locks(self, tmp_path):
        """Two acquire() calls on different paths do NOT contend."""
        from corpus_forge.scanner.filelock import acquire

        lock_a = tmp_path / "a.lock"
        lock_b = tmp_path / "b.lock"
        with acquire(lock_a) as held_a, acquire(lock_b) as held_b:
            assert held_a is True, "lock_a must be acquired"
            assert held_b is True, "lock_b must be acquired (different path)"

    @pytest.mark.requires_unix
    def test_cross_process_contention(self, tmp_path):
        """Two subprocesses contending for the same lock file: exactly one acquires,
        the other gets False or raises IngestRunInProgressError.

        Uses a barrier file to make the contention deterministic:
        1. Process A acquires the lock and writes a 'ready' sentinel file.
        2. Test waits for the sentinel, then spawns Process B with wait=False.
        3. Process B tries to acquire; must fail (exit code 1) or succeed only if A
           already released (race guard: barrier ensures A still holds it).
        """
        lock_file = tmp_path / "cross_proc.lock"
        sentinel_file = tmp_path / "proc_a_ready.sentinel"
        result_file = tmp_path / "proc_b_result.txt"

        # Process A: acquires lock, writes sentinel, holds for 2s then exits
        proc_a_code = textwrap.dedent(f"""
            import time
            from pathlib import Path
            from corpus_forge.scanner.filelock import acquire

            lock_path = Path({str(lock_file)!r})
            sentinel = Path({str(sentinel_file)!r})

            with acquire(lock_path, wait=True) as held:
                sentinel.write_text("ready")
                time.sleep(2.0)
        """)

        # Process B: waits for sentinel, then tries to acquire with wait=False
        proc_b_code = textwrap.dedent(f"""
            import sys
            import time
            from pathlib import Path
            from corpus_forge.scanner.filelock import acquire

            lock_path = Path({str(lock_file)!r})
            sentinel = Path({str(sentinel_file)!r})
            result_file = Path({str(result_file)!r})

            # Wait for process A to hold the lock
            deadline = time.monotonic() + 5.0
            while not sentinel.exists():
                if time.monotonic() > deadline:
                    result_file.write_text("timeout_waiting_for_sentinel")
                    sys.exit(2)
                time.sleep(0.05)

            # Process A holds the lock — now try with wait=False
            try:
                with acquire(lock_path, wait=False) as held:
                    if held is False:
                        result_file.write_text("false")
                        sys.exit(1)
                    else:
                        result_file.write_text("acquired_unexpectedly")
                        sys.exit(0)
            except Exception as exc:
                result_file.write_text(f"exception:{{type(exc).__name__}}")
                sys.exit(1)
        """)

        proc_a = subprocess.Popen(
            [sys.executable, "-c", proc_a_code],
            cwd=str(tmp_path),
        )

        # Start process B in parallel
        proc_b = subprocess.Popen(
            [sys.executable, "-c", proc_b_code],
            cwd=str(tmp_path),
        )

        proc_b.wait(timeout=10)
        proc_a.terminate()
        proc_a.wait(timeout=5)

        # Process B MUST have written result_file (i.e. it successfully imported filelock
        # and ran the contention logic).  A crash before result_file is written means the
        # module is missing — that is the RED state we want to surface as a real failure.
        assert result_file.exists(), (
            f"Process B did not write result_file — it likely crashed before reaching "
            f"the lock contention logic (ImportError?). "
            f"proc_b returncode={proc_b.returncode}"
        )
        result_text = result_file.read_text()
        # Acceptable outcomes: "false" (lock returned False) or "exception:..." (raised)
        # NOT acceptable: "acquired_unexpectedly" or "timeout_waiting_for_sentinel"
        assert result_text in ("false",) or result_text.startswith("exception:"), (
            f"Process B got unexpected result: {result_text!r}. Expected 'false' or 'exception:...'"
        )


# ---------------------------------------------------------------------------
# TestIngestRunInProgressError
# ---------------------------------------------------------------------------


class TestIngestRunInProgressError:
    """IngestRunInProgressError is importable from corpus_forge.backends.base."""

    def test_import_error_class(self):
        """IngestRunInProgressError must be importable from corpus_forge.backends.base."""
        from corpus_forge.backends.base import IngestRunInProgressError  # noqa: F401

    def test_is_exception_subclass(self):
        """IngestRunInProgressError must be a subclass of Exception."""
        from corpus_forge.backends.base import IngestRunInProgressError

        assert issubclass(IngestRunInProgressError, Exception)

    def test_message_contains_host_context(self):
        """IngestRunInProgressError raised with host context carries the phrase."""
        from corpus_forge.backends.base import IngestRunInProgressError

        exc = IngestRunInProgressError("another ingest run is in progress on this host")
        assert "ingest run" in str(exc).lower()


# ---------------------------------------------------------------------------
# TestFileLockWithTimeout
# ---------------------------------------------------------------------------


class TestFileLockWithTimeout:
    """filelock.acquire with timeout parameter."""

    @pytest.mark.requires_unix
    def test_timeout_raises_or_returns_false_when_expired(self, tmp_path):
        """With wait=True and timeout=0.1s, acquire must fail fast if lock is held."""
        from corpus_forge.scanner.filelock import acquire

        lock_path = tmp_path / "timeout.lock"
        acquired_event = threading.Event()
        release_event = threading.Event()

        def hold_lock():
            with acquire(lock_path, wait=True):
                acquired_event.set()
                release_event.wait(timeout=5.0)

        t = threading.Thread(target=hold_lock)
        t.start()
        acquired_event.wait(timeout=2.0)

        start = time.monotonic()
        try:
            result_ctx = acquire(lock_path, wait=True, timeout=0.1)
            with result_ctx as held:
                assert held is False, "Expected False on timeout"
        except Exception:
            pass  # raising on timeout is also acceptable
        elapsed = time.monotonic() - start

        release_event.set()
        t.join(timeout=3.0)

        # Must not have blocked for more than ~1 second (generous bound)
        assert elapsed < 1.5, f"acquire with timeout=0.1 blocked too long: {elapsed:.2f}s"
