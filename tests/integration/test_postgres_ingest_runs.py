"""Integration tests — SR-T2: Postgres backend CRUD for ingest-run state.

These tests exercise the seven new methods on ``PostgresBackend`` that
persist and query ``corpus.ingest_runs`` + ``corpus.ingest_run_sources``.

The SR-G1 migration (``0017_ingest_runs``) creates the tables.  These
tests call ``backend.migrate()`` which will apply that migration; if the
migration does not yet exist, tests will fail at ``migrate()`` time with
an Alembic error rather than an ``AttributeError`` on the methods
themselves.  Both are acceptable RED states.

RED condition
-------------
``PostgresBackend`` does not yet implement any of:
    start_ingest_run, update_ingest_run, finish_ingest_run,
    latest_ingest_run, latest_unfinished_ingest_run,
    upsert_ingest_run_source, find_source_last_scanned_at

Every test will fail with ``AttributeError`` (missing method) OR with an
Alembic ``CommandError`` (missing 0017_ingest_runs revision if SR-T1 /
SR-G1 are also still pending).  Both failure modes are the expected RED
state — do not add any production code to fix them.

Test run command:
    uv run pytest tests/integration/test_postgres_ingest_runs.py -q
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import psycopg
import pytest

from corpus_forge.backends.postgres import PostgresBackend

pytestmark = pytest.mark.integration

# ── fixture ──────────────────────────────────────────────────────────────────


@pytest.fixture
def backend(pg_dsn: str) -> PostgresBackend:  # type: ignore[return]
    """Fresh PostgresBackend against the testcontainers PG.

    ``pg_dsn`` drops+recreates the ``corpus`` schema before yielding so
    each test starts with a clean slate.  We also call ``migrate()`` to
    apply all alembic revisions, including the new 0017_ingest_runs
    tables.
    """
    b = PostgresBackend(dsn=pg_dsn)
    b.migrate()
    yield b
    b.close()


def _run_id() -> str:
    """Generate a unique run_id for test isolation."""
    return uuid.uuid4().hex


def _dataset_id(b: PostgresBackend) -> int:
    """Create a minimal dataset and return its id."""
    return b.get_or_create_dataset(name="test-ds", kind="text", description="")


# ── TestStartIngestRun ────────────────────────────────────────────────────────


class TestStartIngestRun:
    """start_ingest_run inserts a row with status='running' and started_at=now."""

    def test_happy_path_row_exists(self, backend: PostgresBackend) -> None:
        """After start_ingest_run, exactly one row exists for the run_id."""
        run_id = _run_id()
        backend.start_ingest_run(
            run_id=run_id,
            host="test-host",
            pid=12345,
            config_digest="abc123",
        )
        rows = backend._execute(
            "SELECT run_id, status, host, pid, config_digest "
            "FROM corpus.ingest_runs WHERE run_id = %s",
            (run_id,),
        )
        assert len(rows) == 1, f"Expected 1 ingest_runs row after start_ingest_run; got {len(rows)}"
        row = rows[0]
        assert row["run_id"] == run_id
        assert row["status"] == "running"
        assert row["host"] == "test-host"
        assert row["pid"] == 12345
        assert row["config_digest"] == "abc123"

    def test_status_is_running(self, backend: PostgresBackend) -> None:
        """start_ingest_run must set status='running', not any other value."""
        run_id = _run_id()
        backend.start_ingest_run(
            run_id=run_id,
            host="h",
            pid=1,
            config_digest="d",
        )
        rows = backend._execute(
            "SELECT status FROM corpus.ingest_runs WHERE run_id = %s",
            (run_id,),
        )
        assert rows[0]["status"] == "running"

    def test_started_at_is_utc_and_recent(self, backend: PostgresBackend) -> None:
        """started_at must be a TIMESTAMPTZ row within the last 5 seconds."""
        before = datetime.now(tz=UTC)
        run_id = _run_id()
        backend.start_ingest_run(
            run_id=run_id,
            host="h",
            pid=1,
            config_digest="d",
        )
        after = datetime.now(tz=UTC)

        rows = backend._execute(
            "SELECT started_at FROM corpus.ingest_runs WHERE run_id = %s",
            (run_id,),
        )
        started_at = rows[0]["started_at"]
        # Postgres returns aware datetime for TIMESTAMPTZ
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=UTC)
        assert before <= started_at <= after + timedelta(seconds=1), (
            f"started_at {started_at!r} is not between {before!r} and {after!r}"
        )

    def test_ended_at_is_null(self, backend: PostgresBackend) -> None:
        """ended_at must be NULL immediately after start_ingest_run."""
        run_id = _run_id()
        backend.start_ingest_run(
            run_id=run_id,
            host="h",
            pid=1,
            config_digest="d",
        )
        rows = backend._execute(
            "SELECT ended_at FROM corpus.ingest_runs WHERE run_id = %s",
            (run_id,),
        )
        assert rows[0]["ended_at"] is None, (
            f"ended_at should be NULL after start_ingest_run; got {rows[0]['ended_at']!r}"
        )

    def test_idempotent_on_conflict_run_id(self, backend: PostgresBackend) -> None:
        """Calling start_ingest_run twice with the same run_id must not
        raise and must not create a duplicate row.

        This covers the --resume reuse path: the coder uses
        INSERT ... ON CONFLICT (run_id) DO UPDATE so the second call
        flips status back to 'running' and bumps last_progress_at
        instead of raising a unique-violation error.
        """
        run_id = _run_id()
        backend.start_ingest_run(
            run_id=run_id,
            host="h",
            pid=1,
            config_digest="d",
        )
        # Must not raise on second call with same run_id
        backend.start_ingest_run(
            run_id=run_id,
            host="h",
            pid=1,
            config_digest="d",
        )
        rows = backend._execute(
            "SELECT COUNT(*) AS n FROM corpus.ingest_runs WHERE run_id = %s",
            (run_id,),
        )
        assert rows[0]["n"] == 1, (
            f"start_ingest_run twice on same run_id must keep exactly 1 row; got {rows[0]['n']}"
        )

    def test_resume_flips_status_back_to_running(self, backend: PostgresBackend) -> None:
        """After finish + restart with same run_id, status reverts to 'running'.

        This is the --resume contract: the old interrupted row is
        recycled rather than a new row being created.
        """
        run_id = _run_id()
        backend.start_ingest_run(
            run_id=run_id,
            host="h",
            pid=1,
            config_digest="d",
        )
        backend.finish_ingest_run(
            run_id=run_id,
            status="interrupted",
        )
        # Resume: call start_ingest_run again with same run_id
        backend.start_ingest_run(
            run_id=run_id,
            host="h",
            pid=1,
            config_digest="d",
        )
        rows = backend._execute(
            "SELECT status, ended_at FROM corpus.ingest_runs WHERE run_id = %s",
            (run_id,),
        )
        assert rows[0]["status"] == "running", (
            f"After resume start_ingest_run, status must be 'running'; got {rows[0]['status']!r}"
        )


# ── TestUpdateIngestRun ───────────────────────────────────────────────────────


class TestUpdateIngestRun:
    """update_ingest_run updates heartbeat fields and last_progress_at."""

    def test_happy_path_fields_updated(self, backend: PostgresBackend) -> None:
        """Fields last_op, last_done, last_total and last_progress_at are updated."""
        run_id = _run_id()
        backend.start_ingest_run(
            run_id=run_id,
            host="h",
            pid=1,
            config_digest="d",
        )
        backend.update_ingest_run(
            run_id,
            last_op="scan",
            last_done=42,
            last_total=100,
        )
        rows = backend._execute(
            "SELECT last_op, last_done, last_total, last_progress_at "
            "FROM corpus.ingest_runs WHERE run_id = %s",
            (run_id,),
        )
        row = rows[0]
        assert row["last_op"] == "scan"
        assert row["last_done"] == 42
        assert row["last_total"] == 100
        assert row["last_progress_at"] is not None

    def test_partial_update_last_done_only(self, backend: PostgresBackend) -> None:
        """None kwargs must not overwrite existing non-None values."""
        run_id = _run_id()
        backend.start_ingest_run(
            run_id=run_id,
            host="h",
            pid=1,
            config_digest="d",
        )
        backend.update_ingest_run(
            run_id,
            last_op="scan",
            last_done=10,
            last_total=50,
        )
        # Update only last_done; last_op and last_total should remain
        backend.update_ingest_run(
            run_id,
            last_done=20,
        )
        rows = backend._execute(
            "SELECT last_op, last_done, last_total FROM corpus.ingest_runs WHERE run_id = %s",
            (run_id,),
        )
        row = rows[0]
        assert row["last_done"] == 20
        # last_op must not have been nulled out
        assert row["last_op"] == "scan", (
            f"Partial update must not overwrite last_op; got {row['last_op']!r}"
        )

    def test_swallows_operational_error(self, backend: PostgresBackend) -> None:
        """update_ingest_run must silently swallow psycopg.OperationalError
        and emit a DEBUG log rather than propagating the exception.

        This is the best-effort heartbeat contract from the design doc.
        """
        run_id = _run_id()
        backend.start_ingest_run(
            run_id=run_id,
            host="h",
            pid=1,
            config_digest="d",
        )

        # Patch _execute to raise OperationalError — simulates a flaky DB

        def _raise(*_args: Any, **_kwargs: Any) -> None:
            raise psycopg.OperationalError("simulated network blip")

        with (
            patch.object(backend, "_execute", side_effect=_raise),
            patch("corpus_forge.backends.postgres.logger") as mock_logger,
        ):
            # Must NOT raise
            backend.update_ingest_run(
                run_id,
                last_done=99,
            )
            # Must have logged at DEBUG level
            assert mock_logger.debug.called, (
                "update_ingest_run must call logger.debug when OperationalError is swallowed"
            )

    def test_last_progress_at_advances(self, backend: PostgresBackend) -> None:
        """A second call to update_ingest_run must produce a later last_progress_at."""
        run_id = _run_id()
        backend.start_ingest_run(
            run_id=run_id,
            host="h",
            pid=1,
            config_digest="d",
        )
        backend.update_ingest_run(run_id, last_done=1)
        rows1 = backend._execute(
            "SELECT last_progress_at FROM corpus.ingest_runs WHERE run_id = %s",
            (run_id,),
        )
        t1 = rows1[0]["last_progress_at"]

        time.sleep(0.05)

        backend.update_ingest_run(run_id, last_done=2)
        rows2 = backend._execute(
            "SELECT last_progress_at FROM corpus.ingest_runs WHERE run_id = %s",
            (run_id,),
        )
        t2 = rows2[0]["last_progress_at"]
        # Normalise tz-naive to UTC for comparison
        if t1 and t1.tzinfo is None:
            t1 = t1.replace(tzinfo=UTC)
        if t2 and t2.tzinfo is None:
            t2 = t2.replace(tzinfo=UTC)
        assert t2 >= t1, f"last_progress_at must advance on second update; t1={t1!r}, t2={t2!r}"


# ── TestFinishIngestRun ───────────────────────────────────────────────────────


class TestFinishIngestRun:
    """finish_ingest_run transitions status and sets ended_at."""

    @pytest.mark.parametrize(
        "final_status",
        ["completed", "interrupted", "failed"],
    )
    def test_finish_sets_status(self, backend: PostgresBackend, final_status: str) -> None:
        """finish_ingest_run must accept all three terminal statuses."""
        run_id = _run_id()
        backend.start_ingest_run(
            run_id=run_id,
            host="h",
            pid=1,
            config_digest="d",
        )
        backend.finish_ingest_run(run_id=run_id, status=final_status)
        rows = backend._execute(
            "SELECT status FROM corpus.ingest_runs WHERE run_id = %s",
            (run_id,),
        )
        assert rows[0]["status"] == final_status

    def test_finish_sets_ended_at(self, backend: PostgresBackend) -> None:
        """ended_at must be populated (non-NULL) after finish_ingest_run."""
        run_id = _run_id()
        backend.start_ingest_run(
            run_id=run_id,
            host="h",
            pid=1,
            config_digest="d",
        )
        before = datetime.now(tz=UTC)
        backend.finish_ingest_run(run_id=run_id, status="completed")
        after = datetime.now(tz=UTC)

        rows = backend._execute(
            "SELECT ended_at FROM corpus.ingest_runs WHERE run_id = %s",
            (run_id,),
        )
        ended_at = rows[0]["ended_at"]
        assert ended_at is not None, "ended_at must be set after finish_ingest_run"
        if ended_at.tzinfo is None:
            ended_at = ended_at.replace(tzinfo=UTC)
        assert before <= ended_at <= after + timedelta(seconds=1)

    def test_finish_failed_stores_error(self, backend: PostgresBackend) -> None:
        """finish_ingest_run with status='failed' must persist the error field."""
        run_id = _run_id()
        backend.start_ingest_run(
            run_id=run_id,
            host="h",
            pid=1,
            config_digest="d",
        )
        backend.finish_ingest_run(
            run_id=run_id,
            status="failed",
            error="Traceback (most recent call last):\n  ...\nRuntimeError: disk full",
        )
        rows = backend._execute(
            "SELECT status, error FROM corpus.ingest_runs WHERE run_id = %s",
            (run_id,),
        )
        assert rows[0]["status"] == "failed"
        assert "RuntimeError" in (rows[0]["error"] or ""), (
            f"error field must contain the traceback summary; got {rows[0]['error']!r}"
        )

    def test_finish_no_error_null(self, backend: PostgresBackend) -> None:
        """finish_ingest_run with no error keyword must leave error as NULL."""
        run_id = _run_id()
        backend.start_ingest_run(
            run_id=run_id,
            host="h",
            pid=1,
            config_digest="d",
        )
        backend.finish_ingest_run(run_id=run_id, status="completed")
        rows = backend._execute(
            "SELECT error FROM corpus.ingest_runs WHERE run_id = %s",
            (run_id,),
        )
        assert rows[0]["error"] is None, (
            f"error must be NULL when no error is provided; got {rows[0]['error']!r}"
        )


# ── TestLatestIngestRun ───────────────────────────────────────────────────────


class TestLatestIngestRun:
    """latest_ingest_run returns the most-recent row by started_at."""

    def test_returns_none_when_empty(self, backend: PostgresBackend) -> None:
        """Returns None when no runs exist."""
        result = backend.latest_ingest_run()
        assert result is None, f"expected None for empty table, got {result!r}"

    def test_returns_the_only_run(self, backend: PostgresBackend) -> None:
        """Returns the sole run when exactly one exists."""
        run_id = _run_id()
        backend.start_ingest_run(
            run_id=run_id,
            host="h",
            pid=1,
            config_digest="d",
        )
        result = backend.latest_ingest_run()
        assert result is not None
        assert result["run_id"] == run_id

    def test_returns_most_recent_of_multiple(self, backend: PostgresBackend) -> None:
        """With multiple runs, returns the one with the latest started_at."""
        run_id_old = _run_id()
        backend.start_ingest_run(
            run_id=run_id_old,
            host="h",
            pid=1,
            config_digest="d1",
        )
        time.sleep(0.05)
        run_id_new = _run_id()
        backend.start_ingest_run(
            run_id=run_id_new,
            host="h",
            pid=2,
            config_digest="d2",
        )
        result = backend.latest_ingest_run()
        assert result is not None
        assert result["run_id"] == run_id_new, (
            f"latest_ingest_run must return the newest run; "
            f"got {result['run_id']!r} expected {run_id_new!r}"
        )

    def test_any_status_is_returned(self, backend: PostgresBackend) -> None:
        """latest_ingest_run must return rows of any status (completed included)."""
        run_id = _run_id()
        backend.start_ingest_run(
            run_id=run_id,
            host="h",
            pid=1,
            config_digest="d",
        )
        backend.finish_ingest_run(run_id=run_id, status="completed")
        result = backend.latest_ingest_run()
        assert result is not None
        assert result["run_id"] == run_id
        assert result["status"] == "completed"

    def test_return_value_is_dict(self, backend: PostgresBackend) -> None:
        """The return value must be a dict (not a Row, Mapping, etc.)."""
        run_id = _run_id()
        backend.start_ingest_run(
            run_id=run_id,
            host="h",
            pid=1,
            config_digest="d",
        )
        result = backend.latest_ingest_run()
        assert isinstance(result, dict), (
            f"latest_ingest_run must return a plain dict; got {type(result)!r}"
        )

    def test_dict_contains_expected_keys(self, backend: PostgresBackend) -> None:
        """The returned dict must contain at least the schema's core columns."""
        run_id = _run_id()
        backend.start_ingest_run(
            run_id=run_id,
            host="h",
            pid=1,
            config_digest="d",
        )
        result = backend.latest_ingest_run()
        assert result is not None
        expected_keys = {
            "run_id",
            "status",
            "host",
            "pid",
            "config_digest",
            "started_at",
            "ended_at",
            "last_progress_at",
            "last_op",
            "last_done",
            "last_total",
            "error",
        }
        missing = expected_keys - set(result.keys())
        assert not missing, f"latest_ingest_run result missing keys: {sorted(missing)}"


# ── TestLatestUnfinishedIngestRun ─────────────────────────────────────────────


class TestLatestUnfinishedIngestRun:
    """latest_unfinished_ingest_run filters to status IN ('running','interrupted')."""

    def test_returns_none_when_empty(self, backend: PostgresBackend) -> None:
        result = backend.latest_unfinished_ingest_run()
        assert result is None

    def test_returns_none_when_only_completed(self, backend: PostgresBackend) -> None:
        """A 'completed' run must NOT be returned."""
        run_id = _run_id()
        backend.start_ingest_run(
            run_id=run_id,
            host="h",
            pid=1,
            config_digest="d",
        )
        backend.finish_ingest_run(run_id=run_id, status="completed")
        result = backend.latest_unfinished_ingest_run()
        assert result is None, (
            f"latest_unfinished_ingest_run must return None when only completed "
            f"runs exist; got {result!r}"
        )

    def test_returns_none_when_only_failed(self, backend: PostgresBackend) -> None:
        """A 'failed' run is finished — must NOT be returned."""
        run_id = _run_id()
        backend.start_ingest_run(
            run_id=run_id,
            host="h",
            pid=1,
            config_digest="d",
        )
        backend.finish_ingest_run(run_id=run_id, status="failed", error="oops")
        result = backend.latest_unfinished_ingest_run()
        assert result is None, (
            f"latest_unfinished_ingest_run must exclude 'failed' runs; got {result!r}"
        )

    def test_returns_running_run(self, backend: PostgresBackend) -> None:
        """A run with status='running' must be returned."""
        run_id = _run_id()
        backend.start_ingest_run(
            run_id=run_id,
            host="h",
            pid=1,
            config_digest="d",
        )
        result = backend.latest_unfinished_ingest_run()
        assert result is not None
        assert result["run_id"] == run_id
        assert result["status"] == "running"

    def test_returns_interrupted_run(self, backend: PostgresBackend) -> None:
        """A run with status='interrupted' must be returned."""
        run_id = _run_id()
        backend.start_ingest_run(
            run_id=run_id,
            host="h",
            pid=1,
            config_digest="d",
        )
        backend.finish_ingest_run(run_id=run_id, status="interrupted")
        result = backend.latest_unfinished_ingest_run()
        assert result is not None
        assert result["run_id"] == run_id
        assert result["status"] == "interrupted"

    def test_excludes_completed_returns_interrupted(self, backend: PostgresBackend) -> None:
        """With one completed run and one interrupted run, returns interrupted."""
        completed_id = _run_id()
        backend.start_ingest_run(
            run_id=completed_id,
            host="h",
            pid=1,
            config_digest="d",
        )
        backend.finish_ingest_run(run_id=completed_id, status="completed")

        time.sleep(0.05)

        interrupted_id = _run_id()
        backend.start_ingest_run(
            run_id=interrupted_id,
            host="h",
            pid=2,
            config_digest="d",
        )
        backend.finish_ingest_run(run_id=interrupted_id, status="interrupted")

        result = backend.latest_unfinished_ingest_run()
        assert result is not None
        assert result["run_id"] == interrupted_id

    def test_returns_most_recent_among_unfinished(self, backend: PostgresBackend) -> None:
        """When two interrupted runs exist, returns the more-recent one."""
        old_id = _run_id()
        backend.start_ingest_run(
            run_id=old_id,
            host="h",
            pid=1,
            config_digest="d",
        )
        backend.finish_ingest_run(run_id=old_id, status="interrupted")

        time.sleep(0.05)

        new_id = _run_id()
        backend.start_ingest_run(
            run_id=new_id,
            host="h",
            pid=2,
            config_digest="d",
        )
        backend.finish_ingest_run(run_id=new_id, status="interrupted")

        result = backend.latest_unfinished_ingest_run()
        assert result is not None
        assert result["run_id"] == new_id


# ── TestUpsertIngestRunSource ─────────────────────────────────────────────────


class TestUpsertIngestRunSource:
    """upsert_ingest_run_source creates/updates ingest_run_sources rows."""

    def test_happy_path_row_exists(self, backend: PostgresBackend) -> None:
        """After upsert, one row exists for (run_id, source_uri_prefix)."""
        run_id = _run_id()
        dataset_id = _dataset_id(backend)
        backend.start_ingest_run(
            run_id=run_id,
            host="h",
            pid=1,
            config_digest="d",
        )
        backend.upsert_ingest_run_source(
            run_id=run_id,
            source_uri_prefix="vault://notes",
            dataset_id=dataset_id,
            docs_seen_delta=10,
            docs_skipped_delta=2,
            docs_failed_delta=1,
            finished=True,
        )
        rows = backend._execute(
            "SELECT * FROM corpus.ingest_run_sources WHERE run_id = %s AND source_uri_prefix = %s",
            (run_id, "vault://notes"),
        )
        assert len(rows) == 1, f"Expected 1 ingest_run_sources row; got {len(rows)}"
        row = rows[0]
        assert row["docs_seen"] == 10
        assert row["docs_skipped"] == 2
        assert row["docs_failed"] == 1

    def test_idempotent_on_second_call_no_duplicate(self, backend: PostgresBackend) -> None:
        """A second call with the same (run_id, source_uri_prefix) MUST
        update, not insert a second row.  The UNIQUE (run_id, source_uri_prefix)
        constraint means a plain INSERT would fail; upsert semantics are
        required.
        """
        run_id = _run_id()
        dataset_id = _dataset_id(backend)
        backend.start_ingest_run(
            run_id=run_id,
            host="h",
            pid=1,
            config_digest="d",
        )
        backend.upsert_ingest_run_source(
            run_id=run_id,
            source_uri_prefix="vault://notes",
            dataset_id=dataset_id,
            docs_seen_delta=5,
        )
        backend.upsert_ingest_run_source(
            run_id=run_id,
            source_uri_prefix="vault://notes",
            dataset_id=dataset_id,
            docs_seen_delta=8,
        )
        rows = backend._execute(
            "SELECT COUNT(*) AS n FROM corpus.ingest_run_sources "
            "WHERE run_id = %s AND source_uri_prefix = %s",
            (run_id, "vault://notes"),
        )
        assert rows[0]["n"] == 1, (
            f"Second upsert_ingest_run_source must not create a duplicate row; "
            f"got {rows[0]['n']} rows"
        )

    def test_deltas_accumulate_on_second_call(self, backend: PostgresBackend) -> None:
        """docs_seen / docs_skipped / docs_failed deltas must accumulate
        across calls (not replace the prior value).
        """
        run_id = _run_id()
        dataset_id = _dataset_id(backend)
        backend.start_ingest_run(
            run_id=run_id,
            host="h",
            pid=1,
            config_digest="d",
        )
        backend.upsert_ingest_run_source(
            run_id=run_id,
            source_uri_prefix="vault://docs",
            dataset_id=dataset_id,
            docs_seen_delta=10,
            docs_skipped_delta=2,
            docs_failed_delta=0,
        )
        backend.upsert_ingest_run_source(
            run_id=run_id,
            source_uri_prefix="vault://docs",
            dataset_id=dataset_id,
            docs_seen_delta=5,
            docs_skipped_delta=1,
            docs_failed_delta=1,
        )
        rows = backend._execute(
            "SELECT docs_seen, docs_skipped, docs_failed "
            "FROM corpus.ingest_run_sources "
            "WHERE run_id = %s AND source_uri_prefix = %s",
            (run_id, "vault://docs"),
        )
        row = rows[0]
        assert row["docs_seen"] == 15, (
            f"docs_seen should accumulate: 10+5=15; got {row['docs_seen']}"
        )
        assert row["docs_skipped"] == 3, (
            f"docs_skipped should accumulate: 2+1=3; got {row['docs_skipped']}"
        )
        assert row["docs_failed"] == 1, (
            f"docs_failed should accumulate: 0+1=1; got {row['docs_failed']}"
        )

    def test_finished_sets_finished_at(self, backend: PostgresBackend) -> None:
        """When finished=True, finished_at must be populated."""
        run_id = _run_id()
        dataset_id = _dataset_id(backend)
        backend.start_ingest_run(
            run_id=run_id,
            host="h",
            pid=1,
            config_digest="d",
        )
        backend.upsert_ingest_run_source(
            run_id=run_id,
            source_uri_prefix="vault://x",
            dataset_id=dataset_id,
            finished=True,
        )
        rows = backend._execute(
            "SELECT finished_at FROM corpus.ingest_run_sources "
            "WHERE run_id = %s AND source_uri_prefix = %s",
            (run_id, "vault://x"),
        )
        assert rows[0]["finished_at"] is not None, "finished_at must be set when finished=True"

    def test_not_finished_leaves_finished_at_null(self, backend: PostgresBackend) -> None:
        """When finished=False (default), finished_at must remain NULL."""
        run_id = _run_id()
        dataset_id = _dataset_id(backend)
        backend.start_ingest_run(
            run_id=run_id,
            host="h",
            pid=1,
            config_digest="d",
        )
        backend.upsert_ingest_run_source(
            run_id=run_id,
            source_uri_prefix="vault://y",
            dataset_id=dataset_id,
            finished=False,
        )
        rows = backend._execute(
            "SELECT finished_at FROM corpus.ingest_run_sources "
            "WHERE run_id = %s AND source_uri_prefix = %s",
            (run_id, "vault://y"),
        )
        assert rows[0]["finished_at"] is None, (
            f"finished_at should be NULL when finished=False; got {rows[0]['finished_at']!r}"
        )

    def test_fk_cascade_delete(self, backend: PostgresBackend) -> None:
        """Deleting an ingest_run row must cascade-delete its source rows."""
        run_id = _run_id()
        dataset_id = _dataset_id(backend)
        backend.start_ingest_run(
            run_id=run_id,
            host="h",
            pid=1,
            config_digest="d",
        )
        backend.upsert_ingest_run_source(
            run_id=run_id,
            source_uri_prefix="vault://cascade",
            dataset_id=dataset_id,
        )
        # Delete the parent run
        backend._execute(
            "DELETE FROM corpus.ingest_runs WHERE run_id = %s",
            (run_id,),
        )
        rows = backend._execute(
            "SELECT COUNT(*) AS n FROM corpus.ingest_run_sources WHERE run_id = %s",
            (run_id,),
        )
        assert rows[0]["n"] == 0, (
            "ON DELETE CASCADE must remove ingest_run_sources rows "
            "when the parent ingest_runs row is deleted"
        )

    def test_zero_deltas_are_valid(self, backend: PostgresBackend) -> None:
        """All delta fields default to 0 — an upsert with no counts must succeed."""
        run_id = _run_id()
        dataset_id = _dataset_id(backend)
        backend.start_ingest_run(
            run_id=run_id,
            host="h",
            pid=1,
            config_digest="d",
        )
        # Should not raise even with all-zero deltas
        backend.upsert_ingest_run_source(
            run_id=run_id,
            source_uri_prefix="vault://zero",
            dataset_id=dataset_id,
        )
        rows = backend._execute(
            "SELECT docs_seen, docs_skipped, docs_failed "
            "FROM corpus.ingest_run_sources WHERE run_id = %s",
            (run_id,),
        )
        row = rows[0]
        assert row["docs_seen"] == 0
        assert row["docs_skipped"] == 0
        assert row["docs_failed"] == 0

    def test_last_scanned_at_stored(self, backend: PostgresBackend) -> None:
        """When last_scanned_at is provided, it must be persisted."""
        run_id = _run_id()
        dataset_id = _dataset_id(backend)
        backend.start_ingest_run(
            run_id=run_id,
            host="h",
            pid=1,
            config_digest="d",
        )
        ts = datetime.now(tz=UTC)
        backend.upsert_ingest_run_source(
            run_id=run_id,
            source_uri_prefix="vault://ts",
            dataset_id=dataset_id,
            last_scanned_at=ts,
            finished=True,
        )
        rows = backend._execute(
            "SELECT last_scanned_at FROM corpus.ingest_run_sources WHERE run_id = %s",
            (run_id,),
        )
        stored = rows[0]["last_scanned_at"]
        assert stored is not None, "last_scanned_at must be stored when provided"


# ── TestFindSourceLastScannedAt ───────────────────────────────────────────────


class TestFindSourceLastScannedAt:
    """find_source_last_scanned_at returns the latest finished_at across runs."""

    def test_returns_none_when_no_runs(self, backend: PostgresBackend) -> None:
        result = backend.find_source_last_scanned_at("vault://never-seen")
        assert result is None, f"Expected None when source has never been scanned; got {result!r}"

    def test_returns_none_when_source_not_finished(self, backend: PostgresBackend) -> None:
        """A source row with finished_at=NULL must not be returned as a candidate."""
        run_id = _run_id()
        dataset_id = _dataset_id(backend)
        backend.start_ingest_run(
            run_id=run_id,
            host="h",
            pid=1,
            config_digest="d",
        )
        backend.upsert_ingest_run_source(
            run_id=run_id,
            source_uri_prefix="vault://in-progress",
            dataset_id=dataset_id,
            finished=False,
        )
        result = backend.find_source_last_scanned_at("vault://in-progress")
        assert result is None, (
            "find_source_last_scanned_at must return None when source has no finished row"
        )

    def test_returns_finished_at_when_source_finished(self, backend: PostgresBackend) -> None:
        """After a finished source upsert in a completed run, find_source_last_scanned_at
        returns the finished_at timestamp."""
        run_id = _run_id()
        dataset_id = _dataset_id(backend)
        backend.start_ingest_run(
            run_id=run_id,
            host="h",
            pid=1,
            config_digest="d",
        )
        before = datetime.now(tz=UTC)
        backend.upsert_ingest_run_source(
            run_id=run_id,
            source_uri_prefix="vault://done",
            dataset_id=dataset_id,
            finished=True,
        )
        after = datetime.now(tz=UTC)
        # The run must be in a terminal status for find_source_last_scanned_at
        # to return the source's finished_at (binding contract: completed/interrupted only).
        backend.finish_ingest_run(run_id=run_id, status="completed")

        result = backend.find_source_last_scanned_at("vault://done")
        assert result is not None
        if result.tzinfo is None:
            result = result.replace(tzinfo=UTC)
        assert before <= result <= after + timedelta(seconds=1)

    def test_returns_max_across_multiple_runs(self, backend: PostgresBackend) -> None:
        """When the same source has finished_at values in two different runs,
        find_source_last_scanned_at must return the more-recent one.
        """
        dataset_id = _dataset_id(backend)

        run_id_1 = _run_id()
        backend.start_ingest_run(
            run_id=run_id_1,
            host="h",
            pid=1,
            config_digest="d",
        )
        backend.upsert_ingest_run_source(
            run_id=run_id_1,
            source_uri_prefix="vault://repeated",
            dataset_id=dataset_id,
            finished=True,
        )
        backend.finish_ingest_run(run_id=run_id_1, status="completed")

        time.sleep(0.05)

        run_id_2 = _run_id()
        backend.start_ingest_run(
            run_id=run_id_2,
            host="h",
            pid=2,
            config_digest="d",
        )
        backend.upsert_ingest_run_source(
            run_id=run_id_2,
            source_uri_prefix="vault://repeated",
            dataset_id=dataset_id,
            finished=True,
        )
        t_run2 = backend._execute(
            "SELECT finished_at FROM corpus.ingest_run_sources "
            "WHERE run_id = %s AND source_uri_prefix = %s",
            (run_id_2, "vault://repeated"),
        )[0]["finished_at"]
        # The run must be in a terminal status for find_source_last_scanned_at
        # to include it (binding contract: completed/interrupted only).
        backend.finish_ingest_run(run_id=run_id_2, status="completed")

        result = backend.find_source_last_scanned_at("vault://repeated")
        assert result is not None

        # Normalise tz info
        if result.tzinfo is None:
            result = result.replace(tzinfo=UTC)
        if t_run2 is not None and t_run2.tzinfo is None:
            t_run2 = t_run2.replace(tzinfo=UTC)

        # Result must be close to the run_2 finished_at (the most recent)
        assert t_run2 is not None
        diff = abs((result - t_run2).total_seconds())
        assert diff < 1.0, (
            f"find_source_last_scanned_at should return the max finished_at "
            f"({t_run2!r}); got {result!r} (diff={diff}s)"
        )

    def test_excludes_unfinished_in_same_run(self, backend: PostgresBackend) -> None:
        """If one run has finished and a later run has NOT finished yet for
        the same source, must return the earlier run's finished_at, not NULL.
        """
        dataset_id = _dataset_id(backend)

        run_id_1 = _run_id()
        backend.start_ingest_run(
            run_id=run_id_1,
            host="h",
            pid=1,
            config_digest="d",
        )
        before = datetime.now(tz=UTC)
        backend.upsert_ingest_run_source(
            run_id=run_id_1,
            source_uri_prefix="vault://partial",
            dataset_id=dataset_id,
            finished=True,
        )
        after = datetime.now(tz=UTC)
        backend.finish_ingest_run(run_id=run_id_1, status="completed")

        # Second run starts but source is still in-progress
        run_id_2 = _run_id()
        backend.start_ingest_run(
            run_id=run_id_2,
            host="h",
            pid=2,
            config_digest="d",
        )
        backend.upsert_ingest_run_source(
            run_id=run_id_2,
            source_uri_prefix="vault://partial",
            dataset_id=dataset_id,
            finished=False,
        )

        result = backend.find_source_last_scanned_at("vault://partial")
        assert result is not None, (
            "find_source_last_scanned_at must return the prior run's "
            "finished_at when current run's source is not finished"
        )
        if result.tzinfo is None:
            result = result.replace(tzinfo=UTC)
        assert before <= result <= after + timedelta(seconds=1)

    def test_different_sources_are_independent(self, backend: PostgresBackend) -> None:
        """find_source_last_scanned_at must only match the given source_uri_prefix."""
        run_id = _run_id()
        dataset_id = _dataset_id(backend)
        backend.start_ingest_run(
            run_id=run_id,
            host="h",
            pid=1,
            config_digest="d",
        )
        backend.upsert_ingest_run_source(
            run_id=run_id,
            source_uri_prefix="vault://source-A",
            dataset_id=dataset_id,
            finished=True,
        )
        # source-B never seen
        result = backend.find_source_last_scanned_at("vault://source-B")
        assert result is None, (
            f"find_source_last_scanned_at for source-B must return None "
            f"when only source-A has been scanned; got {result!r}"
        )

    def test_returns_datetime_type(self, backend: PostgresBackend) -> None:
        """When a result is found, it must be a datetime instance."""
        run_id = _run_id()
        dataset_id = _dataset_id(backend)
        backend.start_ingest_run(
            run_id=run_id,
            host="h",
            pid=1,
            config_digest="d",
        )
        backend.upsert_ingest_run_source(
            run_id=run_id,
            source_uri_prefix="vault://typed",
            dataset_id=dataset_id,
            finished=True,
        )
        # Run must be terminal for find_source_last_scanned_at to include it.
        backend.finish_ingest_run(run_id=run_id, status="completed")
        result = backend.find_source_last_scanned_at("vault://typed")
        assert isinstance(result, datetime), (
            f"find_source_last_scanned_at must return a datetime; got {type(result)!r}"
        )


# ── TestUtcTimestamps ─────────────────────────────────────────────────────────


class TestUtcTimestamps:
    """All timestamp fields must be UTC-aware when returned."""

    def test_started_at_timezone_aware(self, backend: PostgresBackend) -> None:
        run_id = _run_id()
        backend.start_ingest_run(
            run_id=run_id,
            host="h",
            pid=1,
            config_digest="d",
        )
        row = backend.latest_ingest_run()
        assert row is not None
        started_at = row["started_at"]
        # Postgres TIMESTAMPTZ should come back as aware; if naive, it's
        # still UTC by contract — but we assert non-None and tz-correctness.
        assert started_at is not None, "started_at should not be None"
        if started_at.tzinfo is not None:
            # pytz, zoneinfo, or fixed UTC offset — all acceptable
            offset = started_at.utcoffset()
            assert offset is not None and offset.total_seconds() == 0, (
                f"started_at UTC offset should be 0; got {offset}"
            )

    def test_find_source_last_scanned_at_timezone(self, backend: PostgresBackend) -> None:
        """Timestamp returned by find_source_last_scanned_at must be UTC-aware
        or at minimum have a zero UTC offset.
        """
        run_id = _run_id()
        dataset_id = _dataset_id(backend)
        backend.start_ingest_run(
            run_id=run_id,
            host="h",
            pid=1,
            config_digest="d",
        )
        backend.upsert_ingest_run_source(
            run_id=run_id,
            source_uri_prefix="vault://tz-check",
            dataset_id=dataset_id,
            finished=True,
        )
        # Run must be terminal for find_source_last_scanned_at to include it.
        backend.finish_ingest_run(run_id=run_id, status="completed")
        result = backend.find_source_last_scanned_at("vault://tz-check")
        assert result is not None
        if result.tzinfo is not None:
            offset = result.utcoffset()
            assert offset is not None and offset.total_seconds() == 0, (
                f"find_source_last_scanned_at result UTC offset should be 0; got {offset}"
            )
