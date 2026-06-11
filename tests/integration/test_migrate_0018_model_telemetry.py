"""Integration tests for alembic revision 0018_model_telemetry (Postgres path).

Asserts that after ``alembic upgrade 0018_model_telemetry`` against Postgres:

- ``corpus.hosts`` / ``corpus.models`` / ``corpus.model_benchmarks`` exist with
  the contracted column sets, key columns, and the
  ``model_benchmarks(host_id, model_key, measured_at)`` index.
- ``model_benchmarks`` carries FKs to ``hosts(host_id)`` and ``models(model_key)``.
- The migration chains from ``0017_ingest_runs`` and is idempotent on a
  double-run.
- The :class:`PostgresBackend` ``upsert_host`` / ``upsert_models`` helpers
  write + UPSERT correctly (host last_seen bumps; model first_seen is
  preserved via ON CONFLICT DO NOTHING).

Gated on ``requires_docker``; uses the session-scoped ``pg_dsn`` fixture from
the root conftest.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.requires_docker]

_REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]
_ALEMBIC_INI = _REPO_ROOT / "alembic.ini"
_TARGET_REVISION = "0018_model_telemetry"
_PRIOR_REVISION = "0017_ingest_runs"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sa_dsn(dsn: str) -> str:
    return re.sub(r"^postgresql(s?)://", r"postgresql+psycopg\1://", dsn)


def _upgrade_pg(dsn: str, target: str) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "corpus_forge" / "alembic"))
    cfg.set_main_option("sqlalchemy.url", _sa_dsn(dsn))
    command.upgrade(cfg, target)


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


# ---------------------------------------------------------------------------
# Migration schema
# ---------------------------------------------------------------------------


def test_hosts_models_benchmarks_columns(pg_dsn: str) -> None:
    import psycopg

    _reset_pg_schema(pg_dsn)
    _upgrade_pg(pg_dsn, _TARGET_REVISION)

    with psycopg.connect(pg_dsn) as conn:
        hosts = _column_info(conn, "hosts")
        models = _column_info(conn, "models")
        bench = _column_info(conn, "model_benchmarks")

    assert set(hosts) == {"host_id", "hostname", "os", "accelerator", "tailscale_name", "last_seen"}
    assert hosts["accelerator"]["data_type"] == "jsonb"
    assert hosts["last_seen"]["data_type"] == "timestamp with time zone"

    assert set(models) == {"model_key", "kind", "provider", "model_id", "dimension", "first_seen"}
    assert models["dimension"]["data_type"] == "integer"

    assert set(bench) == {
        "id",
        "host_id",
        "model_key",
        "source",
        "transport",
        "device",
        "batch_size",
        "sample_chunks",
        "chunks_per_s",
        "tokens_per_s",
        "latency_p50_ms",
        "latency_p95_ms",
        "measured_at",
    }
    assert bench["id"]["data_type"] == "bigint"
    assert bench["chunks_per_s"]["data_type"] == "numeric"


def test_model_benchmarks_index_and_fks(pg_dsn: str) -> None:
    import psycopg

    _reset_pg_schema(pg_dsn)
    _upgrade_pg(pg_dsn, _TARGET_REVISION)

    with psycopg.connect(pg_dsn) as conn:
        idx = _index_defs(conn, "model_benchmarks")
        fks = _fk_targets(conn, "model_benchmarks")

    covering = [
        d for d in idx.values() if "host_id" in d and "model_key" in d and "measured_at" in d
    ]
    assert covering, f"no (host_id, model_key, measured_at) index; got {idx}"
    assert fks.get("host_id") == "hosts"
    assert fks.get("model_key") == "models"


def test_upgrade_is_idempotent(pg_dsn: str) -> None:
    import psycopg

    _reset_pg_schema(pg_dsn)
    _upgrade_pg(pg_dsn, _TARGET_REVISION)
    # Rewind the version pin and re-run — must not raise.
    with psycopg.connect(pg_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("UPDATE corpus.alembic_version SET version_num = %s", (_PRIOR_REVISION,))
    _upgrade_pg(pg_dsn, _TARGET_REVISION)
    with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema = 'corpus' AND table_name = 'model_benchmarks'"
        )
        assert cur.fetchone()[0] == 1


# ---------------------------------------------------------------------------
# PostgresBackend upsert helpers
# ---------------------------------------------------------------------------


def test_postgres_upsert_host_and_models(pg_dsn: str) -> None:
    from corpus_forge.backends.postgres import PostgresBackend

    _reset_pg_schema(pg_dsn)
    _upgrade_pg(pg_dsn, _TARGET_REVISION)
    backend = PostgresBackend(dsn=pg_dsn, schema="corpus")

    backend.upsert_host(
        host_id="h1",
        hostname="alpha",
        os="Linux-x",
        accelerator={"kind": "cuda", "vram_mb": 24576},
    )
    rows = backend._execute("SELECT hostname, accelerator, last_seen FROM corpus.hosts")
    assert len(rows) == 1
    assert rows[0]["hostname"] == "alpha"
    # JSONB round-trips to a dict via psycopg.
    assert rows[0]["accelerator"] == {"kind": "cuda", "vram_mb": 24576}
    first_last_seen = rows[0]["last_seen"]

    # Re-upsert bumps last_seen + updates hostname in place (no duplicate).
    backend.upsert_host(host_id="h1", hostname="renamed", os="o", accelerator=None)
    rows = backend._execute("SELECT hostname, last_seen FROM corpus.hosts")
    assert len(rows) == 1
    assert rows[0]["hostname"] == "renamed"
    assert rows[0]["last_seen"] >= first_last_seen

    # Models: first_seen preserved across re-insert (ON CONFLICT DO NOTHING).
    backend.upsert_models(
        [
            {
                "model_key": "openai:m",
                "kind": "embedder",
                "provider": "openai",
                "model_id": "m",
                "dimension": 1536,
            }
        ]
    )
    rows = backend._execute("SELECT dimension, first_seen FROM corpus.models")
    original_first_seen = rows[0]["first_seen"]
    backend.upsert_models(
        [
            {
                "model_key": "openai:m",
                "kind": "embedder",
                "provider": "openai",
                "model_id": "m",
                "dimension": 9999,
            }
        ]
    )
    rows = backend._execute("SELECT dimension, first_seen FROM corpus.models")
    assert len(rows) == 1
    assert rows[0]["dimension"] == 1536
    assert rows[0]["first_seen"] == original_first_seen


def test_postgres_upsert_models_empty_is_noop(pg_dsn: str) -> None:
    from corpus_forge.backends.postgres import PostgresBackend

    _reset_pg_schema(pg_dsn)
    _upgrade_pg(pg_dsn, _TARGET_REVISION)
    backend = PostgresBackend(dsn=pg_dsn, schema="corpus")
    backend.upsert_models([])
    rows = backend._execute("SELECT COUNT(*) AS n FROM corpus.models")
    assert rows[0]["n"] == 0


# ---------------------------------------------------------------------------
# PostgresBackend read helpers (rfc-fleet-1 items 6/7) — latest-per-(host,model)
# ---------------------------------------------------------------------------


def _seed_telemetry(backend: object) -> None:
    """Seed two hosts + one model with multiple benchmark rows.

    h1 benchmarks st:m1 twice (older 10.0, then fresher 99.0); h2 once
    (500.0).  st:m2 is registered but never benchmarked.  Mirrors the
    SQLite read-helper unit fixture so the two dialects pin identical
    "latest per pair" semantics.
    """
    import time

    backend.upsert_host(host_id="h1", hostname="mac", os="macOS", accelerator={"kind": "mps"})
    backend.upsert_host(
        host_id="h2",
        hostname="gb10",
        os="Linux",
        accelerator={"kind": "cuda", "device_name": "GB10", "vram_mb": 20480},
    )
    backend.upsert_models(
        [
            {
                "model_key": "st:m1",
                "kind": "embedder",
                "provider": "st",
                "model_id": "m1",
                "dimension": 384,
            },
            {
                "model_key": "st:m2",
                "kind": "embedder",
                "provider": "st",
                "model_id": "m2",
                "dimension": 768,
            },
        ]
    )
    backend.insert_model_benchmark(
        host_id="h1",
        model_key="st:m1",
        source="bench",
        transport="local",
        device="mps",
        batch_size=32,
        sample_chunks=64,
        chunks_per_s=10.0,
    )
    time.sleep(0.01)
    backend.insert_model_benchmark(
        host_id="h1",
        model_key="st:m1",
        source="embed-run",
        transport="local",
        device="mps",
        batch_size=32,
        sample_chunks=64,
        chunks_per_s=99.0,
    )
    backend.insert_model_benchmark(
        host_id="h2",
        model_key="st:m1",
        source="bench",
        transport="local",
        device="cuda",
        batch_size=64,
        sample_chunks=64,
        chunks_per_s=500.0,
    )


def test_postgres_list_models_latest_per_host_model(pg_dsn: str) -> None:
    from corpus_forge.backends.postgres import PostgresBackend

    _reset_pg_schema(pg_dsn)
    # Behavioral test of the live backend: migrate to HEAD so the
    # current insert/list methods (which reference 0021's cold_start_s)
    # match the schema. The 0018-contract tests above stay pinned at 0018.
    _upgrade_pg(pg_dsn, "head")
    backend = PostgresBackend(dsn=pg_dsn, schema="corpus")
    _seed_telemetry(backend)

    rows = backend.list_models_with_latest_benchmark()
    # h1's older 10.0 row must NOT win — the fresher 99.0 does.
    h1 = [r for r in rows if r["model_key"] == "st:m1" and r["host_id"] == "h1"]
    assert len(h1) == 1
    assert float(h1[0]["chunks_per_s"]) == 99.0
    assert h1[0]["source"] == "embed-run"
    # st:m2 was never benchmarked — still present, no host.
    m2 = [r for r in rows if r["model_key"] == "st:m2"]
    assert len(m2) == 1
    assert m2[0]["host_id"] is None
    assert m2[0]["chunks_per_s"] is None


def test_postgres_list_hosts_latest_rate(pg_dsn: str) -> None:
    from corpus_forge.backends.postgres import PostgresBackend

    _reset_pg_schema(pg_dsn)
    # Behavioral test — migrate to HEAD (see note in
    # test_postgres_list_models_latest_per_host_model).
    _upgrade_pg(pg_dsn, "head")
    backend = PostgresBackend(dsn=pg_dsn, schema="corpus")
    _seed_telemetry(backend)

    hosts = {r["host_id"]: r for r in backend.list_hosts_with_latest_rate()}
    assert hosts["h1"]["models"] == 1
    assert float(hosts["h1"]["latest_chunks_per_s"]) == 99.0
    assert float(hosts["h2"]["latest_chunks_per_s"]) == 500.0


def test_postgres_model_benchmark_stats(pg_dsn: str) -> None:
    from corpus_forge.backends.postgres import PostgresBackend

    _reset_pg_schema(pg_dsn)
    # Behavioral test — migrate to HEAD (see note in
    # test_postgres_list_models_latest_per_host_model).
    _upgrade_pg(pg_dsn, "head")
    backend = PostgresBackend(dsn=pg_dsn, schema="corpus")
    # Empty table first.
    assert backend.model_benchmark_stats() == {"count": 0, "freshest": None}
    _seed_telemetry(backend)
    stats = backend.model_benchmark_stats()
    assert stats["count"] == 3
    assert stats["freshest"] is not None
