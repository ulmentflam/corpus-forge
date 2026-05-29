"""Integration tests — DR-T4: mark_stale_runs on PostgresBackend.

Exercises the new ``mark_stale_runs(threshold_seconds, *, host=None) -> int``
method on ``PostgresBackend``.

RED condition
-------------
``mark_stale_runs`` does not yet exist on ``PostgresBackend``.
Every test will fail with ``AttributeError`` (missing method) until DR-G5
adds the implementation.

Test run command:
    uv run pytest tests/integration/test_postgres_mark_stale_runs.py -q --no-cov
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import psycopg
import pytest

from corpus_forge.backends.postgres import PostgresBackend

pytestmark = pytest.mark.integration


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def backend(pg_dsn: str) -> PostgresBackend:  # type: ignore[return]
    """Fresh PostgresBackend against the testcontainers PG.

    ``pg_dsn`` drops+recreates the ``corpus`` schema before yielding so
    each test starts with a clean slate.  We call ``migrate()`` to apply
    all Alembic revisions, including the ingest_runs tables.
    """
    b = PostgresBackend(dsn=pg_dsn)
    b.migrate()
    yield b
    b.close()


# ── helpers ───────────────────────────────────────────────────────────────────


def _run_id() -> str:
    return uuid.uuid4().hex


def _insert_run(
    backend: PostgresBackend,
    *,
    run_id: str,
    host: str = "test-host",
    pid: int = 12345,
    status: str = "running",
    last_progress_at: datetime,
) -> None:
    """Insert an ingest_runs row with an explicit last_progress_at timestamp.

    ``start_ingest_run`` always sets last_progress_at=NOW(), so we bypass
    it for stale-run tests and write the row directly.
    """
    backend._execute(
        """
        INSERT INTO corpus.ingest_runs
            (run_id, host, pid, config_digest, status, started_at, last_progress_at)
        VALUES (%s, %s, %s, %s, %s, NOW(), %s)
        ON CONFLICT (run_id) DO UPDATE
            SET status = EXCLUDED.status,
                last_progress_at = EXCLUDED.last_progress_at,
                ended_at = NULL,
                error = NULL
        """,
        (run_id, host, pid, "testdigest", status, last_progress_at),
    )


def _fetch_run(backend: PostgresBackend, run_id: str) -> dict:
    rows = backend._execute(
        "SELECT run_id, status, error, ended_at, last_progress_at, host, pid "
        "FROM corpus.ingest_runs WHERE run_id = %s",
        (run_id,),
    )
    assert rows, f"Expected a row for run_id={run_id!r}"
    return dict(rows[0])


def _now() -> datetime:
    return datetime.now(tz=UTC)


# ── TestMarkStaleRuns ─────────────────────────────────────────────────────────


class TestMarkStaleRuns:
    """Behavioural tests for PostgresBackend.mark_stale_runs."""

    # ── Happy path — marks old running rows ──────────────────────────────────

    def test_marks_old_running_as_failed(self, backend: PostgresBackend) -> None:
        """A running row whose last_progress_at is older than threshold is
        transitioned to 'failed' and the method returns 1."""
        run_id = _run_id()
        stale_ts = _now() - timedelta(seconds=1000)
        _insert_run(backend, run_id=run_id, host="host-A", pid=99, last_progress_at=stale_ts)

        result = backend.mark_stale_runs(900)

        assert result == 1, f"Expected 1 row transitioned; got {result}"
        row = _fetch_run(backend, run_id)
        assert row["status"] == "failed", (
            f"Expected status='failed' after mark_stale_runs; got {row['status']!r}"
        )
        assert row["ended_at"] is not None, "ended_at must be set after transition to failed"
        assert row["error"] is not None, "error must be set after transition to failed"
        assert "stale heartbeat" in row["error"], (
            f"error must mention 'stale heartbeat'; got {row['error']!r}"
        )

    def test_does_not_touch_young_running(self, backend: PostgresBackend) -> None:
        """A running row whose last_progress_at is within the threshold is
        NOT touched and the method returns 0."""
        run_id = _run_id()
        fresh_ts = _now() - timedelta(seconds=60)
        _insert_run(backend, run_id=run_id, last_progress_at=fresh_ts)

        result = backend.mark_stale_runs(900)

        assert result == 0, f"Expected 0 rows transitioned; got {result}"
        row = _fetch_run(backend, run_id)
        assert row["status"] == "running", (
            f"Young running row must remain 'running'; got {row['status']!r}"
        )

    # ── Status filtering — only 'running' is eligible ────────────────────────

    def test_does_not_touch_completed(self, backend: PostgresBackend) -> None:
        """A completed row is NOT eligible regardless of age."""
        run_id = _run_id()
        ancient = _now() - timedelta(seconds=99999)
        _insert_run(backend, run_id=run_id, status="completed", last_progress_at=ancient)

        result = backend.mark_stale_runs(900)

        assert result == 0, f"Expected 0 for completed row; got {result}"
        row = _fetch_run(backend, run_id)
        assert row["status"] == "completed", "completed row must not be touched"

    def test_does_not_touch_failed(self, backend: PostgresBackend) -> None:
        """An already-failed row is NOT eligible regardless of age."""
        run_id = _run_id()
        ancient = _now() - timedelta(seconds=99999)
        _insert_run(backend, run_id=run_id, status="failed", last_progress_at=ancient)
        # Also stamp an error so it looks like a real failed row
        backend._execute(
            "UPDATE corpus.ingest_runs SET error = 'prior failure' WHERE run_id = %s",
            (run_id,),
        )

        result = backend.mark_stale_runs(900)

        assert result == 0, f"Expected 0 for already-failed row; got {result}"

    def test_does_not_touch_interrupted(self, backend: PostgresBackend) -> None:
        """An interrupted row is NEVER touched by mark_stale_runs.

        'interrupted' is sticky for --resume; only 'running' is 'alive but dead'.
        Per principal decision #6.
        """
        run_id = _run_id()
        ancient = _now() - timedelta(seconds=99999)
        _insert_run(backend, run_id=run_id, status="interrupted", last_progress_at=ancient)

        result = backend.mark_stale_runs(900)

        assert result == 0, f"Expected 0 for interrupted row (must never be touched); got {result}"
        row = _fetch_run(backend, run_id)
        assert row["status"] == "interrupted", (
            f"interrupted row must remain unchanged; got {row['status']!r}"
        )

    # ── Empty table ───────────────────────────────────────────────────────────

    def test_empty_table_returns_zero(self, backend: PostgresBackend) -> None:
        """When the table has no rows, mark_stale_runs returns 0."""
        result = backend.mark_stale_runs(900)
        assert result == 0, f"Expected 0 on empty table; got {result}"

    # ── threshold_seconds boundary (disabled case) ────────────────────────────

    def test_threshold_zero_noop(self, backend: PostgresBackend) -> None:
        """threshold_seconds=0.0 is the 'disabled' sentinel — must return 0
        and never touch any row."""
        run_id = _run_id()
        ancient = _now() - timedelta(seconds=99999)
        _insert_run(backend, run_id=run_id, last_progress_at=ancient)

        result = backend.mark_stale_runs(0.0)

        assert result == 0, f"Expected 0 when threshold=0.0; got {result}"
        row = _fetch_run(backend, run_id)
        assert row["status"] == "running", "Row must not be touched when threshold=0.0"

    def test_threshold_negative_noop(self, backend: PostgresBackend) -> None:
        """threshold_seconds < 0 is also a no-op short-circuit — returns 0."""
        run_id = _run_id()
        ancient = _now() - timedelta(seconds=99999)
        _insert_run(backend, run_id=run_id, last_progress_at=ancient)

        result = backend.mark_stale_runs(-5)

        assert result == 0, f"Expected 0 when threshold=-5; got {result}"
        row = _fetch_run(backend, run_id)
        assert row["status"] == "running", "Row must not be touched when threshold<0"

    # ── host filter ───────────────────────────────────────────────────────────

    def test_host_filter_marks_only_matching_host(self, backend: PostgresBackend) -> None:
        """When host='A' is given, only A's stale running rows are transitioned."""
        run_A = _run_id()
        run_B = _run_id()
        ancient = _now() - timedelta(seconds=1000)
        _insert_run(backend, run_id=run_A, host="host-A", pid=1, last_progress_at=ancient)
        _insert_run(backend, run_id=run_B, host="host-B", pid=2, last_progress_at=ancient)

        result = backend.mark_stale_runs(900, host="host-A")

        assert result == 1, f"Expected 1 row transitioned (host-A only); got {result}"
        row_A = _fetch_run(backend, run_A)
        row_B = _fetch_run(backend, run_B)
        assert row_A["status"] == "failed", f"host-A row must be failed; got {row_A['status']!r}"
        assert row_B["status"] == "running", (
            f"host-B row must remain running; got {row_B['status']!r}"
        )

    def test_host_none_filter_marks_all_hosts(self, backend: PostgresBackend) -> None:
        """When host=None (default), all stale running rows across all hosts
        are transitioned."""
        run_A = _run_id()
        run_B = _run_id()
        ancient = _now() - timedelta(seconds=1000)
        _insert_run(backend, run_id=run_A, host="host-A", pid=1, last_progress_at=ancient)
        _insert_run(backend, run_id=run_B, host="host-B", pid=2, last_progress_at=ancient)

        result = backend.mark_stale_runs(900, host=None)

        assert result == 2, f"Expected 2 rows transitioned (host=None = all); got {result}"
        assert _fetch_run(backend, run_A)["status"] == "failed"
        assert _fetch_run(backend, run_B)["status"] == "failed"

    def test_host_filter_no_match_returns_zero(self, backend: PostgresBackend) -> None:
        """When the given host has no stale running rows, returns 0."""
        run_id = _run_id()
        ancient = _now() - timedelta(seconds=1000)
        _insert_run(backend, run_id=run_id, host="host-A", pid=1, last_progress_at=ancient)

        result = backend.mark_stale_runs(900, host="host-Z")

        assert result == 0, f"Expected 0 (no host-Z rows); got {result}"
        assert _fetch_run(backend, run_id)["status"] == "running"

    # ── multi-row ─────────────────────────────────────────────────────────────

    def test_multi_row_marks_all_eligible(self, backend: PostgresBackend) -> None:
        """Three stale running rows on the same host — all three are transitioned."""
        ancient = _now() - timedelta(seconds=1000)
        run_ids = [_run_id() for _ in range(3)]
        for rid in run_ids:
            _insert_run(backend, run_id=rid, host="host-A", pid=11, last_progress_at=ancient)

        result = backend.mark_stale_runs(900, host="host-A")

        assert result == 3, f"Expected 3 rows transitioned; got {result}"
        for rid in run_ids:
            assert _fetch_run(backend, rid)["status"] == "failed"

    # ── idempotency ───────────────────────────────────────────────────────────

    def test_idempotent_second_call_returns_zero(self, backend: PostgresBackend) -> None:
        """A second call to mark_stale_runs returns 0 — rows already in 'failed'
        state are not re-selected (status != 'running')."""
        run_id = _run_id()
        ancient = _now() - timedelta(seconds=1000)
        _insert_run(backend, run_id=run_id, last_progress_at=ancient)

        first = backend.mark_stale_runs(900)
        assert first == 1

        second = backend.mark_stale_runs(900)
        assert second == 0, (
            f"Second mark_stale_runs call must return 0 (rows already failed); got {second}"
        )
        assert _fetch_run(backend, run_id)["status"] == "failed"

    # ── error message format (principal decision #7) ──────────────────────────

    def test_error_message_format(self, backend: PostgresBackend) -> None:
        """The error column must match the exact regex from principal decision #7.

        Format: 'stale heartbeat: last progress > Ns ago; host HOST/pid PID presumed dead'
        """
        run_id = _run_id()
        ancient = _now() - timedelta(seconds=1000)
        _insert_run(
            backend,
            run_id=run_id,
            host="deadbox",
            pid=42,
            last_progress_at=ancient,
        )

        backend.mark_stale_runs(900)

        row = _fetch_run(backend, run_id)
        error_msg = row["error"]
        assert error_msg is not None, "error field must be set after stale transition"
        pattern = r"^stale heartbeat: last progress > \d+s ago; host \S+/pid \d+ presumed dead$"
        assert re.match(pattern, error_msg), (
            f"error message does not match expected format:\n"
            f"  pattern: {pattern!r}\n"
            f"  actual:  {error_msg!r}"
        )

    def test_error_message_captures_prior_host_and_pid(self, backend: PostgresBackend) -> None:
        """The error string must embed the PRIOR host and pid from the row
        (audit trail — not some default value)."""
        run_id = _run_id()
        ancient = _now() - timedelta(seconds=1000)
        _insert_run(
            backend,
            run_id=run_id,
            host="specific-host",
            pid=99999,
            last_progress_at=ancient,
        )

        backend.mark_stale_runs(900)

        row = _fetch_run(backend, run_id)
        assert "specific-host" in row["error"], (
            f"error must contain prior host 'specific-host'; got {row['error']!r}"
        )
        assert "99999" in row["error"], f"error must contain prior pid 99999; got {row['error']!r}"

    # ── last_progress_at preserved (audit trail) ──────────────────────────────

    def test_last_progress_at_unchanged_after_mark(self, backend: PostgresBackend) -> None:
        """mark_stale_runs must NOT modify last_progress_at — it is audit evidence."""
        run_id = _run_id()
        ancient = _now() - timedelta(seconds=1000)
        _insert_run(backend, run_id=run_id, last_progress_at=ancient)

        backend.mark_stale_runs(900)

        row = _fetch_run(backend, run_id)
        stored_lpa = row["last_progress_at"]
        if stored_lpa.tzinfo is None:
            stored_lpa = stored_lpa.replace(tzinfo=UTC)
        if ancient.tzinfo is None:
            ancient = ancient.replace(tzinfo=UTC)
        diff = abs((stored_lpa - ancient).total_seconds())
        assert diff < 2.0, (
            f"last_progress_at must be preserved (audit trail); "
            f"original={ancient!r}, after_mark={stored_lpa!r}, diff={diff}s"
        )

    # ── OperationalError swallowed ────────────────────────────────────────────

    def test_operationalerror_swallowed_returns_zero(self, backend: PostgresBackend) -> None:
        """When _execute raises psycopg.OperationalError, mark_stale_runs must
        swallow it and return 0 (best-effort idiom — ingest must still start)."""
        run_id = _run_id()
        ancient = _now() - timedelta(seconds=1000)
        _insert_run(backend, run_id=run_id, last_progress_at=ancient)

        def _raise(*_args: Any, **_kwargs: Any) -> None:
            raise psycopg.OperationalError("simulated network failure")

        with patch.object(backend, "_execute", side_effect=_raise):
            result = backend.mark_stale_runs(900)

        assert result == 0, (
            f"mark_stale_runs must return 0 when OperationalError is swallowed; got {result}"
        )

    # ── threshold boundary — exactly at threshold ─────────────────────────────

    def test_row_at_exact_threshold_boundary(self, backend: PostgresBackend) -> None:
        """A row whose age equals threshold_seconds is stale (strictly: age > threshold
        per C5 semantics — we use 2 * threshold to avoid boundary ambiguity in wall-clock
        tests, but also verify a 1-second-over-threshold row is caught)."""
        run_id = _run_id()
        # 901 seconds old with threshold=900 — must be stale
        slightly_stale = _now() - timedelta(seconds=901)
        _insert_run(backend, run_id=run_id, last_progress_at=slightly_stale)

        result = backend.mark_stale_runs(900)

        assert result == 1, f"A row 901s old with threshold=900 must be caught; got {result}"
        assert _fetch_run(backend, run_id)["status"] == "failed"

    # ── return type ───────────────────────────────────────────────────────────

    def test_return_type_is_int(self, backend: PostgresBackend) -> None:
        """mark_stale_runs must always return an int (even when 0)."""
        result = backend.mark_stale_runs(900)
        assert isinstance(result, int), f"mark_stale_runs must return int; got {type(result)!r}"
