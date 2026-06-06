"""Integration tests for alembic revision 0020_shared_config (Postgres path).

Two halves, both gated on ``requires_docker`` and using the
session-scoped ``pg_dsn`` fixture:

1. **Migration shape** — after ``alembic upgrade 0020_shared_config``:
   - ``corpus.shared_config`` exists with the contracted column set + types.
   - ``corpus_id`` is the PK with ``DEFAULT 1``.
   - ``published_by`` carries an FK to ``corpus.hosts(host_id)``.
   - The migration chains from ``0019_embed_claims`` and is idempotent.
   - ``downgrade`` drops the table (this revision is NOT forward-only).

2. **Backend helpers** — ``PostgresBackend.get_shared_config`` /
   ``put_shared_config``:
   - Round-trip: None → publish v1 → get → publish v2.
   - Stale-version publish raises ``SharedConfigVersionConflict`` whose
     message says to pull first.
   - Two threads racing the same ``expected_version``: exactly one wins,
     the loser raises ``SharedConfigVersionConflict`` (both first-publish
     and update races).
   - ``published_by`` requires a heartbeated host (FK enforced).
"""

from __future__ import annotations

import re
import threading
from typing import Any

import pytest

from corpus_forge.backends.base import SharedConfigVersionConflict
from corpus_forge.backends.postgres import PostgresBackend

pytestmark = [pytest.mark.integration, pytest.mark.requires_docker]

_REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]
_ALEMBIC_INI = _REPO_ROOT / "alembic.ini"
_TARGET_REVISION = "0020_shared_config"
_PRIOR_REVISION = "0019_embed_claims"


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
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = 'corpus' AND table_name = %s
            ORDER BY ordinal_position
            """,
            (table,),
        )
        rows = cur.fetchall()
    return {r[0]: {"data_type": r[1], "is_nullable": r[2], "column_default": r[3]} for r in rows}


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


def _pk_columns(conn: Any, table: str) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            WHERE tc.constraint_type = 'PRIMARY KEY'
              AND tc.table_schema = 'corpus'
              AND tc.table_name = %s
            """,
            (table,),
        )
        return {r[0] for r in cur.fetchall()}


# ── Migration shape ───────────────────────────────────────────────────────────


def test_shared_config_columns(pg_dsn: str) -> None:
    import psycopg

    _reset_pg_schema(pg_dsn)
    _alembic_to(pg_dsn, _TARGET_REVISION)

    with psycopg.connect(pg_dsn) as conn:
        cols = _column_info(conn, "shared_config")

    assert set(cols) == {
        "corpus_id",
        "version",
        "body",
        "published_by",
        "published_at",
    }
    assert cols["corpus_id"]["data_type"] == "integer"
    assert cols["corpus_id"]["column_default"] is not None
    assert "1" in str(cols["corpus_id"]["column_default"])
    assert cols["version"]["data_type"] == "integer"
    assert cols["version"]["is_nullable"] == "NO"
    assert cols["body"]["data_type"] == "jsonb"
    assert cols["body"]["is_nullable"] == "NO"
    assert cols["published_at"]["data_type"] == "timestamp with time zone"


def test_shared_config_pk_and_fk(pg_dsn: str) -> None:
    import psycopg

    _reset_pg_schema(pg_dsn)
    _alembic_to(pg_dsn, _TARGET_REVISION)

    with psycopg.connect(pg_dsn) as conn:
        assert _pk_columns(conn, "shared_config") == {"corpus_id"}
        fks = _fk_targets(conn, "shared_config")
    assert fks.get("published_by") == "hosts"


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
            "WHERE table_schema = 'corpus' AND table_name = 'shared_config'"
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
            "WHERE table_schema = 'corpus' AND table_name = 'shared_config'"
        )
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT version_num FROM corpus.alembic_version")
        assert cur.fetchone()[0] == _PRIOR_REVISION


# ── Backend helpers ───────────────────────────────────────────────────────────


@pytest.fixture
def backend(pg_dsn: str) -> PostgresBackend:  # type: ignore[return]
    b = PostgresBackend(dsn=pg_dsn)
    b.migrate()
    # Clear any shared_config row left by a prior test (migrate() does not
    # reset the schema; helper tests assume an empty start).
    b._execute("DELETE FROM corpus.shared_config")
    # published_by FKs corpus.hosts — pre-register every host id used below
    # (mirrors the embed_claims claim tests' heartbeat-then-write pattern).
    for host_id in ("publisher", "peer", "racer-A", "racer-B"):
        b.upsert_host(host_id=host_id, hostname=host_id, os="test", accelerator=None)
    yield b
    b.close()


def test_get_returns_none_when_never_published(backend: PostgresBackend) -> None:
    assert backend.get_shared_config() is None


def test_publish_get_roundtrip(backend: PostgresBackend) -> None:
    body_v1 = {"federation": {"enabled": True}, "embed": {"lanes": ["a", "b"]}}
    v1 = backend.put_shared_config(body_v1, expected_version=0, published_by="publisher")
    assert v1 == 1

    got = backend.get_shared_config()
    assert got is not None
    version, body = got
    assert version == 1
    assert body == body_v1

    # Update on top of v1 → v2.
    body_v2 = {"federation": {"enabled": True}, "embed": {"lanes": ["c"]}}
    v2 = backend.put_shared_config(body_v2, expected_version=1, published_by="peer")
    assert v2 == 2

    got2 = backend.get_shared_config()
    assert got2 == (2, body_v2)


def test_stale_publish_raises_pull_first(backend: PostgresBackend) -> None:
    backend.put_shared_config({"k": 1}, expected_version=0, published_by="publisher")
    backend.put_shared_config({"k": 2}, expected_version=1, published_by="peer")
    # A publisher still at version 1 tries to write — the DB is at 2.
    with pytest.raises(SharedConfigVersionConflict) as exc:
        backend.put_shared_config({"k": 3}, expected_version=1, published_by="publisher")
    assert "pull" in str(exc.value).lower()
    # The losing write did not clobber: DB still at v2 with the peer's body.
    assert backend.get_shared_config() == (2, {"k": 2})


def test_first_publish_conflict_on_existing_row(backend: PostgresBackend) -> None:
    """A second host doing a first-publish (expected_version=0) after one
    already landed loses the ON CONFLICT race rather than overwriting."""
    backend.put_shared_config({"k": "first"}, expected_version=0, published_by="publisher")
    with pytest.raises(SharedConfigVersionConflict):
        backend.put_shared_config({"k": "second"}, expected_version=0, published_by="peer")
    assert backend.get_shared_config() == (1, {"k": "first"})


def test_published_by_fk_requires_heartbeated_host(backend: PostgresBackend) -> None:
    import psycopg

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        backend.put_shared_config({"k": 1}, expected_version=0, published_by="ghost-host")


def _race_first_publish(backend: PostgresBackend, pg_dsn: str, hosts: tuple[str, str]) -> None:
    """Two threads both attempt the first publish at expected_version=0."""
    barrier = threading.Barrier(2)
    winners: list[int] = []
    conflicts: list[SharedConfigVersionConflict] = []
    other_errors: list[BaseException] = []
    lock = threading.Lock()

    def worker(host_id: str) -> None:
        b = PostgresBackend(dsn=pg_dsn)
        try:
            barrier.wait(timeout=10)
            v = b.put_shared_config({"by": host_id}, expected_version=0, published_by=host_id)
            with lock:
                winners.append(v)
        except SharedConfigVersionConflict as exc:
            with lock:
                conflicts.append(exc)
        except BaseException as exc:  # surfaced below — don't swallow in thread
            with lock:
                other_errors.append(exc)
        finally:
            b.close()

    threads = [threading.Thread(target=worker, args=(h,)) for h in hosts]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not other_errors, f"unexpected error in race worker: {other_errors!r}"
    assert winners == [1], f"expected exactly one winner at v1; got {winners!r}"
    assert len(conflicts) == 1, f"expected exactly one conflict loser; got {conflicts!r}"


def test_two_thread_first_publish_race_one_wins(backend: PostgresBackend, pg_dsn: str) -> None:
    _race_first_publish(backend, pg_dsn, ("racer-A", "racer-B"))
    assert backend.get_shared_config() is not None


def test_two_thread_update_race_one_wins(backend: PostgresBackend, pg_dsn: str) -> None:
    """Both racers pull v1, both try to publish v2: exactly one lands."""
    backend.put_shared_config({"seed": True}, expected_version=0, published_by="publisher")

    barrier = threading.Barrier(2)
    winners: list[int] = []
    conflicts: list[SharedConfigVersionConflict] = []
    other_errors: list[BaseException] = []
    lock = threading.Lock()

    def worker(host_id: str) -> None:
        b = PostgresBackend(dsn=pg_dsn)
        try:
            barrier.wait(timeout=10)
            v = b.put_shared_config({"by": host_id}, expected_version=1, published_by=host_id)
            with lock:
                winners.append(v)
        except SharedConfigVersionConflict as exc:
            with lock:
                conflicts.append(exc)
        except BaseException as exc:
            with lock:
                other_errors.append(exc)
        finally:
            b.close()

    threads = [threading.Thread(target=worker, args=(h,)) for h in ("racer-A", "racer-B")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not other_errors, f"unexpected error in race worker: {other_errors!r}"
    assert winners == [2], f"expected exactly one winner at v2; got {winners!r}"
    assert len(conflicts) == 1, f"expected exactly one conflict loser; got {conflicts!r}"
    version, _ = backend.get_shared_config()  # type: ignore[misc]
    assert version == 2
