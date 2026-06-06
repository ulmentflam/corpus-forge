"""Integration tests for alembic revision 0019_embed_claims (Postgres path).

Assert that after ``alembic upgrade 0019_embed_claims`` against Postgres:

- ``corpus.embed_claims`` exists with the contracted column set + types.
- The ``(embedder_id, chunk_id)`` UNIQUE constraint and the
  ``embed_claims(lease_until)`` index both exist.
- ``host_id`` carries an FK to ``corpus.hosts(host_id)``.
- The migration chains from ``0018_model_telemetry`` and is idempotent.
- ``downgrade`` drops the table (this revision is NOT forward-only).

Gated on ``requires_docker``; uses the session-scoped ``pg_dsn`` fixture.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.requires_docker]

_REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]
_ALEMBIC_INI = _REPO_ROOT / "alembic.ini"
_TARGET_REVISION = "0019_embed_claims"
_PRIOR_REVISION = "0018_model_telemetry"


def _sa_dsn(dsn: str) -> str:
    return re.sub(r"^postgresql(s?)://", r"postgresql+psycopg\1://", dsn)


def _alembic_to(dsn: str, target: str) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "corpus_forge" / "alembic"))
    cfg.set_main_option("sqlalchemy.url", _sa_dsn(dsn))
    command.upgrade(cfg, target)


def _alembic_downgrade(dsn: str, target: str) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "corpus_forge" / "alembic"))
    cfg.set_main_option("sqlalchemy.url", _sa_dsn(dsn))
    command.downgrade(cfg, target)


def _reset_pg_schema(dsn: str) -> None:
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS corpus CASCADE")
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cur.execute("CREATE SCHEMA IF NOT EXISTS corpus")


def _column_info(conn: Any, table: str) -> dict[str, dict[str, str | None]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'corpus' AND table_name = %s
            ORDER BY ordinal_position
            """,
            (table,),
        )
        rows = cur.fetchall()
    return {r[0]: {"data_type": r[1], "is_nullable": r[2]} for r in rows}


def _index_defs(conn: Any, table: str) -> dict[str, str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE schemaname = 'corpus' AND tablename = %s",
            (table,),
        )
        return {r[0]: r[1] for r in cur.fetchall()}


def _fk_targets(conn: Any, table: str) -> dict[str, str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT kcu.column_name, ccu.table_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            JOIN information_schema.constraint_column_usage ccu
              ON tc.constraint_name = ccu.constraint_name
             AND tc.table_schema = ccu.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_schema = 'corpus'
              AND tc.table_name = %s
            """,
            (table,),
        )
        return {r[0]: r[1] for r in cur.fetchall()}


def _has_unique(conn: Any, table: str, *columns: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT tc.constraint_name, kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            WHERE tc.constraint_type = 'UNIQUE'
              AND tc.table_schema = 'corpus'
              AND tc.table_name = %s
            """,
            (table,),
        )
        by_constraint: dict[str, set[str]] = {}
        for cname, col in cur.fetchall():
            by_constraint.setdefault(cname, set()).add(col)
    return any(cols == set(columns) for cols in by_constraint.values())


def test_embed_claims_columns(pg_dsn: str) -> None:
    import psycopg

    _reset_pg_schema(pg_dsn)
    _alembic_to(pg_dsn, _TARGET_REVISION)

    with psycopg.connect(pg_dsn) as conn:
        cols = _column_info(conn, "embed_claims")

    assert set(cols) == {
        "claim_id",
        "embedder_id",
        "chunk_id",
        "host_id",
        "claimed_at",
        "lease_until",
    }
    assert cols["claim_id"]["data_type"] == "bigint"
    assert cols["embedder_id"]["data_type"] == "integer"
    assert cols["chunk_id"]["data_type"] == "bigint"
    assert cols["lease_until"]["data_type"] == "timestamp with time zone"
    assert cols["lease_until"]["is_nullable"] == "NO"


def test_embed_claims_unique_index_and_fk(pg_dsn: str) -> None:
    import psycopg

    _reset_pg_schema(pg_dsn)
    _alembic_to(pg_dsn, _TARGET_REVISION)

    with psycopg.connect(pg_dsn) as conn:
        idx = _index_defs(conn, "embed_claims")
        fks = _fk_targets(conn, "embed_claims")
        assert _has_unique(conn, "embed_claims", "embedder_id", "chunk_id")

    lease_idx = [d for d in idx.values() if "lease_until" in d]
    assert lease_idx, f"no lease_until index; got {idx}"
    assert fks.get("host_id") == "hosts"


def test_upgrade_is_idempotent(pg_dsn: str) -> None:
    import psycopg

    _reset_pg_schema(pg_dsn)
    _alembic_to(pg_dsn, _TARGET_REVISION)
    with psycopg.connect(pg_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("UPDATE corpus.alembic_version SET version_num = %s", (_PRIOR_REVISION,))
    _alembic_to(pg_dsn, _TARGET_REVISION)  # re-run must not raise
    with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema = 'corpus' AND table_name = 'embed_claims'"
        )
        assert cur.fetchone()[0] == 1


def test_downgrade_drops_table(pg_dsn: str) -> None:
    import psycopg

    _reset_pg_schema(pg_dsn)
    _alembic_to(pg_dsn, _TARGET_REVISION)
    _alembic_downgrade(pg_dsn, _PRIOR_REVISION)
    with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema = 'corpus' AND table_name = 'embed_claims'"
        )
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT version_num FROM corpus.alembic_version")
        assert cur.fetchone()[0] == _PRIOR_REVISION
