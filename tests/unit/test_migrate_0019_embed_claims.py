"""Unit tests for alembic revision 0019_embed_claims (SQLite path).

Run at the unit tier (no Docker) by driving ``alembic upgrade`` against an
in-process SQLite database in ``tmp_path``.  Assert that after upgrading to
``0019_embed_claims``:

- ``embed_claims`` exists with the contracted column set + key columns.
- The ``embed_claims(lease_until)`` index exists.
- The ``(embedder_id, chunk_id)`` UNIQUE constraint is enforced.
- ``alembic upgrade`` is idempotent (rewind the version pin + re-run).
- ``alembic downgrade -1`` rolls the pin back to ``0018_model_telemetry``
  AND actually drops the ``embed_claims`` table (this revision is NOT
  forward-only — it owns ephemeral coordination state).

The Postgres equivalents live in ``tests/integration`` behind ``requires_docker``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ALEMBIC_INI = _REPO_ROOT / "alembic.ini"
_VERSIONS_DIR = _REPO_ROOT / "corpus_forge" / "alembic" / "versions"

_TARGET_REVISION = "0019_embed_claims"
_PRIOR_REVISION = "0018_model_telemetry"


def _alembic_cfg(db_path: Path) -> Config:
    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "corpus_forge" / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def _upgrade(db_path: Path, target: str = _TARGET_REVISION) -> None:
    command.upgrade(_alembic_cfg(db_path), target)


def _downgrade(db_path: Path, target: str) -> None:
    command.downgrade(_alembic_cfg(db_path), target)


def _col_map(conn: sqlite3.Connection, table: str) -> dict[str, dict]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {
        row[1]: {"type": row[2].upper(), "notnull": bool(row[3]), "pk": bool(row[5])}
        for row in rows
    }


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchall()
    return len(rows) == 1


def _index_covers(conn: sqlite3.Connection, table: str, *columns: str) -> bool:
    rows = conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name=?",
        (table,),
    ).fetchall()
    for _name, sql in rows:
        text = (sql or "").lower()
        if all(c.lower() in text for c in columns):
            return True
    return False


def test_embed_claims_table_and_columns(tmp_path: Path) -> None:
    db = tmp_path / "claims.db"
    _upgrade(db)
    with sqlite3.connect(str(db)) as conn:
        assert _table_exists(conn, "embed_claims")
        cols = _col_map(conn, "embed_claims")
    assert set(cols) == {
        "claim_id",
        "embedder_id",
        "chunk_id",
        "host_id",
        "claimed_at",
        "lease_until",
    }
    assert cols["claim_id"]["pk"], "embed_claims.claim_id must be PRIMARY KEY"
    assert cols["embedder_id"]["notnull"]
    assert cols["chunk_id"]["notnull"]
    assert cols["lease_until"]["notnull"]


def test_lease_until_index_exists(tmp_path: Path) -> None:
    db = tmp_path / "claims_idx.db"
    _upgrade(db)
    with sqlite3.connect(str(db)) as conn:
        assert _index_covers(conn, "embed_claims", "lease_until"), (
            "embed_claims(lease_until) index missing"
        )


def test_unique_embedder_chunk_constraint(tmp_path: Path) -> None:
    """A duplicate (embedder_id, chunk_id) insert must raise IntegrityError."""
    db = tmp_path / "claims_unique.db"
    _upgrade(db)
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            "INSERT INTO embed_claims (embedder_id, chunk_id, host_id, lease_until) "
            "VALUES (1, 100, 'h1', '2026-01-01T00:00:00Z')"
        )
        conn.commit()
        try:
            conn.execute(
                "INSERT INTO embed_claims (embedder_id, chunk_id, host_id, lease_until) "
                "VALUES (1, 100, 'h2', '2026-01-01T00:00:00Z')"
            )
            conn.commit()
            raise AssertionError("expected IntegrityError on duplicate (embedder_id, chunk_id)")
        except sqlite3.IntegrityError:
            pass


def test_upgrade_is_idempotent_on_double_run(tmp_path: Path) -> None:
    db = tmp_path / "claims_idempotent.db"
    _upgrade(db)
    with sqlite3.connect(str(db)) as conn:
        conn.execute(f"UPDATE alembic_version SET version_num = '{_PRIOR_REVISION}'")
        conn.commit()
    _upgrade(db)  # must NOT raise
    with sqlite3.connect(str(db)) as conn:
        assert _table_exists(conn, "embed_claims")


def test_downgrade_drops_table_and_rolls_back_pin(tmp_path: Path) -> None:
    db = tmp_path / "claims_downgrade.db"
    _upgrade(db)
    with sqlite3.connect(str(db)) as conn:
        assert _table_exists(conn, "embed_claims")
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == (
            _TARGET_REVISION
        )
    _downgrade(db, "-1")
    with sqlite3.connect(str(db)) as conn:
        assert not _table_exists(conn, "embed_claims"), (
            "downgrade() must DROP the embed_claims table (ephemeral coordination state)"
        )
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == (
            _PRIOR_REVISION
        )
