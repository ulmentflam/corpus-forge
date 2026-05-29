"""Integration tests — DR-T4: mark_stale_runs on SQLiteBackend.

Mirror of ``test_postgres_mark_stale_runs.py`` against the SQLiteBackend.
No Docker required; all tests use ``tmp_path``.

RED condition
-------------
``mark_stale_runs`` does not yet exist on ``SQLiteBackend``.
Every test will fail with ``AttributeError`` until DR-G5 adds the implementation.

Test run command:
    uv run pytest tests/integration/test_sqlite_mark_stale_runs.py -q --no-cov
"""

from __future__ import annotations

import re
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from corpus_forge.backends.sqlite import SQLiteBackend

# ── helpers ───────────────────────────────────────────────────────────────────


def _make_backend(db_path: Path) -> SQLiteBackend:
    """Create a migrated SQLiteBackend at *db_path*."""
    backend = SQLiteBackend(path=str(db_path))
    backend.migrate()
    return backend


def _run_id() -> str:
    return f"dr-t4-{uuid.uuid4().hex}"


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _now_iso() -> str:
    return _now().isoformat()


def _ts_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat()


def _insert_run(
    db_path: Path,
    *,
    run_id: str,
    host: str = "test-host",
    pid: int = 12345,
    status: str = "running",
    last_progress_at: datetime,
) -> None:
    """Insert an ingest_runs row directly via sqlite3 with an explicit
    last_progress_at timestamp (bypassing start_ingest_run which always
    uses NOW())."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(
            """
            INSERT INTO ingest_runs
                (run_id, host, pid, config_digest, status, started_at, last_progress_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE
                SET status = excluded.status,
                    last_progress_at = excluded.last_progress_at,
                    ended_at = NULL,
                    error = NULL
            """,
            (run_id, host, pid, "testdigest", status, _now_iso(), _ts_iso(last_progress_at)),
        )
        conn.commit()
    finally:
        conn.close()


def _fetch_run(db_path: Path, run_id: str) -> dict:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT run_id, status, error, ended_at, last_progress_at, host, pid "
            "FROM ingest_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, f"Expected a row for run_id={run_id!r}"
    return dict(row)


# ── TestMarkStaleRuns ─────────────────────────────────────────────────────────


class TestMarkStaleRuns:
    """Behavioural tests for SQLiteBackend.mark_stale_runs."""

    # ── Happy path — marks old running rows ──────────────────────────────────

    def test_marks_old_running_as_failed(self, tmp_path: Path) -> None:
        """A running row whose last_progress_at is older than threshold is
        transitioned to 'failed' and the method returns 1."""
        db = tmp_path / "db.sqlite"
        backend = _make_backend(db)
        run_id = _run_id()
        stale_ts = _now() - timedelta(seconds=1000)
        _insert_run(db, run_id=run_id, host="host-A", pid=99, last_progress_at=stale_ts)

        result = backend.mark_stale_runs(900)

        assert result == 1, f"Expected 1 row transitioned; got {result}"
        row = _fetch_run(db, run_id)
        assert row["status"] == "failed", (
            f"Expected status='failed' after mark_stale_runs; got {row['status']!r}"
        )
        assert row["ended_at"] is not None, "ended_at must be set after transition to failed"
        assert row["error"] is not None, "error must be set after transition to failed"
        assert "stale heartbeat" in row["error"], (
            f"error must mention 'stale heartbeat'; got {row['error']!r}"
        )

    def test_does_not_touch_young_running(self, tmp_path: Path) -> None:
        """A running row whose last_progress_at is within the threshold is
        NOT touched and the method returns 0."""
        db = tmp_path / "db.sqlite"
        backend = _make_backend(db)
        run_id = _run_id()
        fresh_ts = _now() - timedelta(seconds=60)
        _insert_run(db, run_id=run_id, last_progress_at=fresh_ts)

        result = backend.mark_stale_runs(900)

        assert result == 0, f"Expected 0 rows transitioned; got {result}"
        row = _fetch_run(db, run_id)
        assert row["status"] == "running", (
            f"Young running row must remain 'running'; got {row['status']!r}"
        )

    # ── Status filtering — only 'running' is eligible ────────────────────────

    def test_does_not_touch_completed(self, tmp_path: Path) -> None:
        """A completed row is NOT eligible regardless of age."""
        db = tmp_path / "db.sqlite"
        backend = _make_backend(db)
        run_id = _run_id()
        ancient = _now() - timedelta(seconds=99999)
        _insert_run(db, run_id=run_id, status="completed", last_progress_at=ancient)

        result = backend.mark_stale_runs(900)

        assert result == 0, f"Expected 0 for completed row; got {result}"
        row = _fetch_run(db, run_id)
        assert row["status"] == "completed", "completed row must not be touched"

    def test_does_not_touch_failed(self, tmp_path: Path) -> None:
        """An already-failed row is NOT eligible regardless of age."""
        db = tmp_path / "db.sqlite"
        backend = _make_backend(db)
        run_id = _run_id()
        ancient = _now() - timedelta(seconds=99999)
        _insert_run(db, run_id=run_id, status="failed", last_progress_at=ancient)

        result = backend.mark_stale_runs(900)

        assert result == 0, f"Expected 0 for already-failed row; got {result}"

    def test_does_not_touch_interrupted(self, tmp_path: Path) -> None:
        """An interrupted row is NEVER touched by mark_stale_runs.

        'interrupted' is sticky for --resume; only 'running' is 'alive but dead'.
        Per principal decision #6.
        """
        db = tmp_path / "db.sqlite"
        backend = _make_backend(db)
        run_id = _run_id()
        ancient = _now() - timedelta(seconds=99999)
        _insert_run(db, run_id=run_id, status="interrupted", last_progress_at=ancient)

        result = backend.mark_stale_runs(900)

        assert result == 0, f"Expected 0 for interrupted row (must never be touched); got {result}"
        row = _fetch_run(db, run_id)
        assert row["status"] == "interrupted", (
            f"interrupted row must remain unchanged; got {row['status']!r}"
        )

    # ── Empty table ───────────────────────────────────────────────────────────

    def test_empty_table_returns_zero(self, tmp_path: Path) -> None:
        """When the table has no rows, mark_stale_runs returns 0."""
        db = tmp_path / "db.sqlite"
        backend = _make_backend(db)
        result = backend.mark_stale_runs(900)
        assert result == 0, f"Expected 0 on empty table; got {result}"

    # ── threshold_seconds boundary (disabled case) ────────────────────────────

    def test_threshold_zero_noop(self, tmp_path: Path) -> None:
        """threshold_seconds=0.0 is the 'disabled' sentinel — must return 0
        and never touch any row."""
        db = tmp_path / "db.sqlite"
        backend = _make_backend(db)
        run_id = _run_id()
        ancient = _now() - timedelta(seconds=99999)
        _insert_run(db, run_id=run_id, last_progress_at=ancient)

        result = backend.mark_stale_runs(0.0)

        assert result == 0, f"Expected 0 when threshold=0.0; got {result}"
        row = _fetch_run(db, run_id)
        assert row["status"] == "running", "Row must not be touched when threshold=0.0"

    def test_threshold_negative_noop(self, tmp_path: Path) -> None:
        """threshold_seconds < 0 is a no-op short-circuit — returns 0."""
        db = tmp_path / "db.sqlite"
        backend = _make_backend(db)
        run_id = _run_id()
        ancient = _now() - timedelta(seconds=99999)
        _insert_run(db, run_id=run_id, last_progress_at=ancient)

        result = backend.mark_stale_runs(-5)

        assert result == 0, f"Expected 0 when threshold=-5; got {result}"
        row = _fetch_run(db, run_id)
        assert row["status"] == "running", "Row must not be touched when threshold<0"

    # ── host filter ───────────────────────────────────────────────────────────

    def test_host_filter_marks_only_matching_host(self, tmp_path: Path) -> None:
        """When host='host-A' is given, only host-A's stale running rows are
        transitioned; host-B remains running."""
        db = tmp_path / "db.sqlite"
        backend = _make_backend(db)
        run_A = _run_id()
        run_B = _run_id()
        ancient = _now() - timedelta(seconds=1000)
        _insert_run(db, run_id=run_A, host="host-A", pid=1, last_progress_at=ancient)
        _insert_run(db, run_id=run_B, host="host-B", pid=2, last_progress_at=ancient)

        result = backend.mark_stale_runs(900, host="host-A")

        assert result == 1, f"Expected 1 row transitioned (host-A only); got {result}"
        assert _fetch_run(db, run_A)["status"] == "failed"
        assert _fetch_run(db, run_B)["status"] == "running"

    def test_host_none_filter_marks_all_hosts(self, tmp_path: Path) -> None:
        """When host=None (default), all stale running rows across all hosts
        are transitioned."""
        db = tmp_path / "db.sqlite"
        backend = _make_backend(db)
        run_A = _run_id()
        run_B = _run_id()
        ancient = _now() - timedelta(seconds=1000)
        _insert_run(db, run_id=run_A, host="host-A", pid=1, last_progress_at=ancient)
        _insert_run(db, run_id=run_B, host="host-B", pid=2, last_progress_at=ancient)

        result = backend.mark_stale_runs(900, host=None)

        assert result == 2, f"Expected 2 rows transitioned (host=None = all); got {result}"
        assert _fetch_run(db, run_A)["status"] == "failed"
        assert _fetch_run(db, run_B)["status"] == "failed"

    def test_host_filter_no_match_returns_zero(self, tmp_path: Path) -> None:
        """When the given host has no stale running rows, returns 0."""
        db = tmp_path / "db.sqlite"
        backend = _make_backend(db)
        run_id = _run_id()
        ancient = _now() - timedelta(seconds=1000)
        _insert_run(db, run_id=run_id, host="host-A", pid=1, last_progress_at=ancient)

        result = backend.mark_stale_runs(900, host="host-Z")

        assert result == 0, f"Expected 0 (no host-Z rows); got {result}"
        assert _fetch_run(db, run_id)["status"] == "running"

    # ── multi-row ─────────────────────────────────────────────────────────────

    def test_multi_row_marks_all_eligible(self, tmp_path: Path) -> None:
        """Three stale running rows on the same host — all three transitioned."""
        db = tmp_path / "db.sqlite"
        backend = _make_backend(db)
        ancient = _now() - timedelta(seconds=1000)
        run_ids = [_run_id() for _ in range(3)]
        for rid in run_ids:
            _insert_run(db, run_id=rid, host="host-A", pid=11, last_progress_at=ancient)

        result = backend.mark_stale_runs(900, host="host-A")

        assert result == 3, f"Expected 3 rows transitioned; got {result}"
        for rid in run_ids:
            assert _fetch_run(db, rid)["status"] == "failed"

    # ── idempotency ───────────────────────────────────────────────────────────

    def test_idempotent_second_call_returns_zero(self, tmp_path: Path) -> None:
        """A second call to mark_stale_runs returns 0 — rows already 'failed'
        are not re-selected."""
        db = tmp_path / "db.sqlite"
        backend = _make_backend(db)
        run_id = _run_id()
        ancient = _now() - timedelta(seconds=1000)
        _insert_run(db, run_id=run_id, last_progress_at=ancient)

        first = backend.mark_stale_runs(900)
        assert first == 1

        second = backend.mark_stale_runs(900)
        assert second == 0, (
            f"Second mark_stale_runs call must return 0 (rows already failed); got {second}"
        )
        assert _fetch_run(db, run_id)["status"] == "failed"

    # ── error message format (principal decision #7) ──────────────────────────

    def test_error_message_format(self, tmp_path: Path) -> None:
        """The error column must match the exact regex from principal decision #7.

        Format: 'stale heartbeat: last progress > Ns ago; host HOST/pid PID presumed dead'
        """
        db = tmp_path / "db.sqlite"
        backend = _make_backend(db)
        run_id = _run_id()
        ancient = _now() - timedelta(seconds=1000)
        _insert_run(db, run_id=run_id, host="deadbox", pid=42, last_progress_at=ancient)

        backend.mark_stale_runs(900)

        row = _fetch_run(db, run_id)
        error_msg = row["error"]
        assert error_msg is not None, "error field must be set after stale transition"
        pattern = r"^stale heartbeat: last progress > \d+s ago; host \S+/pid \d+ presumed dead$"
        assert re.match(pattern, error_msg), (
            f"error message does not match expected format:\n"
            f"  pattern: {pattern!r}\n"
            f"  actual:  {error_msg!r}"
        )

    def test_error_message_captures_prior_host_and_pid(self, tmp_path: Path) -> None:
        """The error string must embed the PRIOR host and pid from the row
        (audit trail — not some default value)."""
        db = tmp_path / "db.sqlite"
        backend = _make_backend(db)
        run_id = _run_id()
        ancient = _now() - timedelta(seconds=1000)
        _insert_run(db, run_id=run_id, host="specific-host", pid=99999, last_progress_at=ancient)

        backend.mark_stale_runs(900)

        row = _fetch_run(db, run_id)
        assert "specific-host" in row["error"], (
            f"error must contain prior host 'specific-host'; got {row['error']!r}"
        )
        assert "99999" in row["error"], f"error must contain prior pid 99999; got {row['error']!r}"

    # ── last_progress_at preserved (audit trail) ──────────────────────────────

    def test_last_progress_at_unchanged_after_mark(self, tmp_path: Path) -> None:
        """mark_stale_runs must NOT modify last_progress_at — it is audit evidence."""
        db = tmp_path / "db.sqlite"
        backend = _make_backend(db)
        run_id = _run_id()
        ancient = _now() - timedelta(seconds=1000)
        _insert_run(db, run_id=run_id, last_progress_at=ancient)

        backend.mark_stale_runs(900)

        row = _fetch_run(db, run_id)
        stored_lpa_raw = row["last_progress_at"]
        assert stored_lpa_raw is not None, "last_progress_at must be present in the row"
        # SQLite stores as ISO string; compare as string prefix (date portion stable)
        ancient_str = ancient.isoformat()[:19]  # e.g. "2026-05-28T10:00:00"
        assert ancient_str[:10] in str(stored_lpa_raw), (
            f"last_progress_at must be preserved (audit trail); "
            f"original={ancient!r}, stored={stored_lpa_raw!r}"
        )

    # ── OperationalError swallowed ────────────────────────────────────────────

    def test_operationalerror_swallowed_returns_zero(self, tmp_path: Path) -> None:
        """When _execute raises sqlite3.OperationalError, mark_stale_runs must
        swallow it and return 0 (best-effort idiom — ingest must still start)."""
        db = tmp_path / "db.sqlite"
        backend = _make_backend(db)
        run_id = _run_id()
        ancient = _now() - timedelta(seconds=1000)
        _insert_run(db, run_id=run_id, last_progress_at=ancient)

        original_execute = backend._execute

        call_count = [0]

        def _raise_on_update(sql: str, *args: Any, **kwargs: Any) -> Any:
            call_count[0] += 1
            # Allow SELECTs through so the threshold>0 guard can check; raise on UPDATE
            if "UPDATE" in sql.upper() and "ingest_runs" in sql.lower():
                raise sqlite3.OperationalError("simulated disk error")
            return original_execute(sql, *args, **kwargs)

        backend._execute = _raise_on_update  # type: ignore[method-assign]

        result = backend.mark_stale_runs(900)

        assert result == 0, (
            f"mark_stale_runs must return 0 when OperationalError is swallowed; got {result}"
        )

    # ── threshold boundary — one second over threshold ────────────────────────

    def test_row_at_one_second_over_threshold_is_caught(self, tmp_path: Path) -> None:
        """A row 901 seconds old with threshold=900 must be caught."""
        db = tmp_path / "db.sqlite"
        backend = _make_backend(db)
        run_id = _run_id()
        slightly_stale = _now() - timedelta(seconds=901)
        _insert_run(db, run_id=run_id, last_progress_at=slightly_stale)

        result = backend.mark_stale_runs(900)

        assert result == 1, f"A row 901s old with threshold=900 must be caught; got {result}"
        assert _fetch_run(db, run_id)["status"] == "failed"

    # ── return type ───────────────────────────────────────────────────────────

    def test_return_type_is_int(self, tmp_path: Path) -> None:
        """mark_stale_runs must always return an int (even when 0)."""
        db = tmp_path / "db.sqlite"
        backend = _make_backend(db)
        result = backend.mark_stale_runs(900)
        assert isinstance(result, int), f"mark_stale_runs must return int; got {type(result)!r}"

    # ── mixed eligible / ineligible rows ─────────────────────────────────────

    def test_mixed_statuses_only_running_marked(self, tmp_path: Path) -> None:
        """When the table has running + interrupted + completed + failed rows,
        only the stale running row is transitioned."""
        db = tmp_path / "db.sqlite"
        backend = _make_backend(db)
        ancient = _now() - timedelta(seconds=1000)
        fresh = _now() - timedelta(seconds=60)

        run_stale = _run_id()
        run_fresh = _run_id()
        run_interrupted = _run_id()
        run_completed = _run_id()
        run_failed = _run_id()

        _insert_run(db, run_id=run_stale, status="running", last_progress_at=ancient)
        _insert_run(db, run_id=run_fresh, status="running", last_progress_at=fresh)
        _insert_run(db, run_id=run_interrupted, status="interrupted", last_progress_at=ancient)
        _insert_run(db, run_id=run_completed, status="completed", last_progress_at=ancient)
        _insert_run(db, run_id=run_failed, status="failed", last_progress_at=ancient)

        result = backend.mark_stale_runs(900)

        assert result == 1, f"Expected exactly 1 row (stale running only); got {result}"
        assert _fetch_run(db, run_stale)["status"] == "failed"
        assert _fetch_run(db, run_fresh)["status"] == "running"
        assert _fetch_run(db, run_interrupted)["status"] == "interrupted"
        assert _fetch_run(db, run_completed)["status"] == "completed"
        assert _fetch_run(db, run_failed)["status"] == "failed"
