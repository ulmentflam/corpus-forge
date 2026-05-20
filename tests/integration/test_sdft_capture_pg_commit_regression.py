"""Regression tests for corpus_forge.sdft.capture Postgres commit bug.

Bug history (fixed in commit d5568a7)
------------------------------------
The Postgres branch of ``record_demonstration`` issued an
``INSERT ... ON CONFLICT DO NOTHING RETURNING id`` against the corpus
schema but never called ``conn.commit()``. The path was::

    with backend._get_connection() as conn:        # opens conn
        result = capture.record_demonstration(conn, ...)
                                            ^^^^
                                            # INSERT runs inside an
                                            # uncommitted transaction
        # conn.commit() — MISSING on the Postgres branch
    # context manager closes conn → uncommitted tx → row rolled back

The MCP write tool returned a ``demonstration_id`` from the INSERT's
``RETURNING id`` clause, but a subsequent ``SELECT id FROM
corpus.sdft_demonstrations`` from any fresh connection (or even from a
later ``backend._execute``) saw nothing — a phantom id.

These tests pin the behavior so the regression doesn't come back:

1. ``test_durable_after_connection_close`` — exercise the documented
   bug shape directly: capture a demo, close the connection, reopen a
   new connection, verify the row is visible.
2. ``test_two_writes_share_a_committed_dedup_row`` — second identical
   write hits the ``ON CONFLICT DO NOTHING`` branch; that branch must
   ALSO succeed (no implicit dependency on the in-flight row being
   visible to the same transaction).
3. ``test_record_demonstration_calls_commit`` — a unit-level white-box
   check using a spy connection that records each commit() call.
4. ``test_dispatch_then_select_by_id_round_trip`` — the original
   end-to-end shape that failed pre-fix: dispatcher returns an id,
   verify the row is fetchable by that id from a fresh connection.

Cross-reference: tests/integration/test_mcp_record_demonstration.py
TestRecordDemonstrationPostgres adds the same coverage via the MCP
dispatcher; this file targets the lower-level ``capture`` function
directly so a regression in capture.py is detected even if the MCP
surface drifts.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.requires_docker]


# ---------------------------------------------------------------------------
# Schema reset + migration helpers (mirrors the pattern in
# test_mcp_record_demonstration.py::TestRecordDemonstrationPostgres so the
# regression tests are self-contained).
# ---------------------------------------------------------------------------


def _reset_and_migrate(pg_dsn: str) -> None:
    """Drop + recreate the corpus schema, then run alembic up to head."""
    import psycopg
    from alembic import command
    from alembic.config import Config as _AlembicConfig

    with psycopg.connect(pg_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS corpus CASCADE")
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cur.execute("CREATE SCHEMA IF NOT EXISTS corpus")

    repo_root = Path(__file__).resolve().parents[2]
    cfg = _AlembicConfig(str(repo_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(repo_root / "corpus_forge" / "alembic"))
    cfg.set_main_option(
        "sqlalchemy.url",
        re.sub(r"^postgresql(s?)://", r"postgresql+psycopg\1://", pg_dsn),
    )
    command.upgrade(cfg, "head")


def _seed_dataset(pg_dsn: str, name: str = "pg-commit-regression") -> int:
    """Insert a dataset row and return its id."""
    import psycopg

    with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO corpus.datasets (name, kind, description) "
            "VALUES (%s, %s, %s) RETURNING id",
            (name, "text", "Regression test for SDFT capture PG commit bug"),
        )
        ds_id = cur.fetchone()[0]
        conn.commit()
    return int(ds_id)


def _make_demo_kwargs(dataset_id: int) -> dict[str, Any]:
    return {
        "query": "What is the bound state energy of a hydrogen atom?",
        "student_messages": [
            {"role": "assistant", "content": "About -13.6 eV in the ground state."},
        ],
        "teacher_messages": [
            {"role": "user", "content": "Derive it from the Bohr model."},
        ],
        "target": "Ground-state energy = -13.6 eV (Bohr model, n=1).",
        "source": "record_demonstration",
        "dataset_id": dataset_id,
        "trace_id": "pg-commit-regression-trace",
    }


# ---------------------------------------------------------------------------
# Regression 1 — durability across connection close
# ---------------------------------------------------------------------------


def test_durable_after_connection_close(pg_dsn: str) -> None:
    """A demonstration written on conn-A must be visible on conn-B.

    The bug: capture.record_demonstration returned a fresh demonstration_id
    from `INSERT ... RETURNING id`, but the row was rolled back when the
    connection's context manager closed the conn without commit(). A
    second connection saw no row at that id — a phantom write.
    """
    import psycopg

    from corpus_forge.sdft.capture import record_demonstration

    _reset_and_migrate(pg_dsn)
    ds_id = _seed_dataset(pg_dsn)

    # Connection A: write the demonstration, capture the returned id,
    # then explicitly close conn-A.
    conn_a = psycopg.connect(pg_dsn)
    try:
        result = record_demonstration(conn_a, **_make_demo_kwargs(ds_id))
    finally:
        conn_a.close()

    assert "demonstration_id" in result
    demo_id = int(result["demonstration_id"])
    assert result["deduped"] is False, (
        "First write must NOT be flagged as deduped — there is nothing to dedup against."
    )

    # Connection B: independent connection — must see the row at demo_id.
    with psycopg.connect(pg_dsn) as conn_b, conn_b.cursor() as cur:
        cur.execute(
            "SELECT id, dataset_id, source, target FROM corpus.sdft_demonstrations WHERE id = %s",
            (demo_id,),
        )
        row = cur.fetchone()

    assert row is not None, (
        f"Regression: demonstration_id={demo_id} returned by capture is a phantom; "
        f"the row is not visible on a fresh connection. capture.py must commit() "
        f"after the Postgres INSERT."
    )
    assert int(row[0]) == demo_id
    assert int(row[1]) == ds_id
    assert row[2] == "record_demonstration"
    assert row[3].startswith("Ground-state energy")


# ---------------------------------------------------------------------------
# Regression 2 — dedup branch needs the prior row to be committed
# ---------------------------------------------------------------------------


def test_two_writes_share_a_committed_dedup_row(pg_dsn: str) -> None:
    """The second identical call must hit `ON CONFLICT DO NOTHING`.

    Without commit() on the first call, two outcomes are possible
    depending on transaction isolation: either both inserts succeed
    (rolled back later) or the second call sees a stale snapshot and
    inserts a duplicate. Either way the table ends up wrong. The fix
    commits after every successful insert so the unique constraint on
    content_hash is enforced at next-write time.
    """
    import psycopg

    from corpus_forge.sdft.capture import record_demonstration

    _reset_and_migrate(pg_dsn)
    ds_id = _seed_dataset(pg_dsn, name="pg-commit-regression-dedup")
    kwargs = _make_demo_kwargs(ds_id)

    # Two sequential writes on separate connections — what the MCP
    # dispatcher actually does because backend._get_connection opens
    # and closes a fresh connection each call.
    conn_a = psycopg.connect(pg_dsn)
    try:
        first = record_demonstration(conn_a, **kwargs)
    finally:
        conn_a.close()

    conn_b = psycopg.connect(pg_dsn)
    try:
        second = record_demonstration(conn_b, **kwargs)
    finally:
        conn_b.close()

    assert first["deduped"] is False
    assert second["deduped"] is True, (
        "Second identical call must hit ON CONFLICT DO NOTHING and return "
        "deduped=True. If this fails, the first call's row wasn't committed "
        "before the second call ran."
    )
    assert first["demonstration_id"] == second["demonstration_id"]

    with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM corpus.sdft_demonstrations WHERE dataset_id = %s",
            (ds_id,),
        )
        count = int(cur.fetchone()[0])
    assert count == 1, f"Expected exactly 1 row after dedup; got {count}."


# ---------------------------------------------------------------------------
# Regression 3 — white-box: capture must invoke conn.commit()
# ---------------------------------------------------------------------------


def test_record_demonstration_calls_commit(pg_dsn: str) -> None:
    """capture.record_demonstration must call conn.commit() at least once.

    This is the direct white-box regression: a refactor that drops the
    commit call leaves the row in an open transaction. We monkey-patch
    the real connection's ``commit`` method to count invocations while
    keeping ``type(conn).__module__`` correctly reporting psycopg so the
    Postgres branch is taken.
    """
    import psycopg

    from corpus_forge.sdft.capture import record_demonstration

    _reset_and_migrate(pg_dsn)
    ds_id = _seed_dataset(pg_dsn, name="pg-commit-regression-spy")

    conn = psycopg.connect(pg_dsn)
    commit_count = 0
    rollback_count = 0
    real_commit = conn.commit
    real_rollback = conn.rollback

    def _counted_commit() -> None:
        nonlocal commit_count
        commit_count += 1
        real_commit()

    def _counted_rollback() -> None:
        nonlocal rollback_count
        rollback_count += 1
        real_rollback()

    # Bind the counted shims as instance attributes so the capture
    # function's `conn.commit()` lookup finds them first.
    conn.commit = _counted_commit  # type: ignore[method-assign]
    conn.rollback = _counted_rollback  # type: ignore[method-assign]
    try:
        result = record_demonstration(conn, **_make_demo_kwargs(ds_id))
    finally:
        # Restore real methods before close() so psycopg's teardown
        # doesn't accidentally double-count.
        conn.commit = real_commit  # type: ignore[method-assign]
        conn.rollback = real_rollback  # type: ignore[method-assign]
        conn.close()

    assert "demonstration_id" in result
    assert commit_count >= 1, (
        f"capture.record_demonstration must call conn.commit() at least once; "
        f"got {commit_count} commits and {rollback_count} rollbacks. "
        f"Regression: the Postgres INSERT path silently dropped its commit, "
        f"leaving the row in an uncommitted transaction."
    )


# ---------------------------------------------------------------------------
# Regression 4 — full round-trip via the dispatcher returns a usable id
# ---------------------------------------------------------------------------


def test_dispatch_then_select_by_id_round_trip(pg_dsn: str) -> None:
    """The original failure shape: ``demonstration_id`` returned by the
    MCP dispatcher must be fetchable in a separate ``backend._execute``.

    Before the fix this test failed at the SELECT step with an empty
    result set because the INSERT had been rolled back.
    """
    from corpus_forge.backends.postgres import PostgresBackend
    from corpus_forge.sdft.capture import record_demonstration

    _reset_and_migrate(pg_dsn)
    ds_id = _seed_dataset(pg_dsn, name="pg-commit-regression-rt")
    backend = PostgresBackend(dsn=pg_dsn, schema="corpus")

    with backend._get_connection() as conn:
        result = record_demonstration(conn, **_make_demo_kwargs(ds_id))

    demo_id = int(result["demonstration_id"])
    rows = backend._execute(
        "SELECT id, source, dataset_id FROM corpus.sdft_demonstrations WHERE id = %s",
        (demo_id,),
    )
    assert rows, (
        f"Regression: demonstration_id={demo_id} returned by record_demonstration "
        f"is a phantom id — no row found via backend._execute after the dispatcher "
        f"reported success. The Postgres INSERT branch must call conn.commit()."
    )
    assert int(rows[0]["id"]) == demo_id
    assert int(rows[0]["dataset_id"]) == ds_id
    assert rows[0]["source"] == "record_demonstration"
