"""Unit tests for alembic revision 0018_model_telemetry (SQLite path).

These run at the unit tier (no Docker) by driving ``alembic upgrade`` against
an in-process SQLite database in ``tmp_path``.  They assert that after
upgrading to ``0018_model_telemetry``:

- ``hosts`` / ``models`` / ``model_benchmarks`` tables exist with the exact
  contracted column sets and key columns.
- The ``model_benchmarks(host_id, model_key, measured_at)`` index exists.
- ``downgrade()`` is forward-only (single ``pass`` body per project convention).
- ``alembic upgrade`` is idempotent (rewind the version pin + re-run, no error).
- ``alembic downgrade -1`` rolls the version pin back to ``0017_ingest_runs``.

The Postgres equivalents live in ``tests/integration`` behind ``requires_docker``.
"""

from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ALEMBIC_INI = _REPO_ROOT / "alembic.ini"
_VERSIONS_DIR = _REPO_ROOT / "corpus_forge" / "alembic" / "versions"
_MIGRATION_FILE = _VERSIONS_DIR / "0018_model_telemetry.py"

_TARGET_REVISION = "0018_model_telemetry"
_PRIOR_REVISION = "0017_ingest_runs"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Module attributes
# ---------------------------------------------------------------------------


def test_downgrade_is_forward_only_pass() -> None:
    """``downgrade()`` body must be a single ``pass`` (forward-only convention)."""
    tree = ast.parse(_MIGRATION_FILE.read_text(encoding="utf-8"))
    downgrade = next(
        (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "downgrade"),
        None,
    )
    assert downgrade is not None, "no downgrade() found in 0018_model_telemetry.py"
    assert len(downgrade.body) == 1 and isinstance(downgrade.body[0], ast.Pass), (
        "downgrade() body must be a single ``pass`` statement (forward-only convention)"
    )


# ---------------------------------------------------------------------------
# hosts
# ---------------------------------------------------------------------------


def test_hosts_table_and_columns(tmp_path: Path) -> None:
    db = tmp_path / "telemetry_hosts.db"
    _upgrade(db)
    with sqlite3.connect(str(db)) as conn:
        assert _table_exists(conn, "hosts")
        cols = _col_map(conn, "hosts")
    assert set(cols) == {
        "host_id",
        "hostname",
        "os",
        "accelerator",
        "tailscale_name",
        "last_seen",
    }
    assert cols["host_id"]["pk"], "hosts.host_id must be PRIMARY KEY"
    assert "TEXT" in cols["host_id"]["type"]
    # accelerator is JSON-as-TEXT on SQLite
    assert "TEXT" in cols["accelerator"]["type"]


# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------


def test_models_table_and_columns(tmp_path: Path) -> None:
    db = tmp_path / "telemetry_models.db"
    _upgrade(db)
    with sqlite3.connect(str(db)) as conn:
        assert _table_exists(conn, "models")
        cols = _col_map(conn, "models")
    assert set(cols) == {
        "model_key",
        "kind",
        "provider",
        "model_id",
        "dimension",
        "first_seen",
    }
    assert cols["model_key"]["pk"], "models.model_key must be PRIMARY KEY"
    assert "INTEGER" in cols["dimension"]["type"]


def test_models_model_key_unique_constraint(tmp_path: Path) -> None:
    """A duplicate model_key insert must raise IntegrityError (PK uniqueness)."""
    db = tmp_path / "telemetry_models_unique.db"
    _upgrade(db)
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            "INSERT INTO models (model_key, kind, provider, model_id) "
            "VALUES ('openai:gpt', 'llm', 'openai', 'gpt')"
        )
        conn.commit()
        try:
            conn.execute(
                "INSERT INTO models (model_key, kind, provider, model_id) "
                "VALUES ('openai:gpt', 'llm', 'openai', 'gpt')"
            )
            conn.commit()
            raise AssertionError("expected IntegrityError on duplicate model_key")
        except sqlite3.IntegrityError:
            pass


# ---------------------------------------------------------------------------
# model_benchmarks
# ---------------------------------------------------------------------------


def test_model_benchmarks_table_and_columns(tmp_path: Path) -> None:
    db = tmp_path / "telemetry_bench.db"
    _upgrade(db)
    with sqlite3.connect(str(db)) as conn:
        assert _table_exists(conn, "model_benchmarks")
        cols = _col_map(conn, "model_benchmarks")
    assert set(cols) == {
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
    assert cols["id"]["pk"] and "INTEGER" in cols["id"]["type"]


def test_model_benchmarks_index_exists(tmp_path: Path) -> None:
    db = tmp_path / "telemetry_bench_idx.db"
    _upgrade(db)
    with sqlite3.connect(str(db)) as conn:
        assert _index_covers(conn, "model_benchmarks", "host_id", "model_key", "measured_at"), (
            "model_benchmarks(host_id, model_key, measured_at) index missing"
        )


# ---------------------------------------------------------------------------
# idempotency + downgrade
# ---------------------------------------------------------------------------


def test_upgrade_is_idempotent_on_double_run(tmp_path: Path) -> None:
    db = tmp_path / "telemetry_idempotent.db"
    _upgrade(db)
    # Rewind the version pin so upgrade() fires again against an existing schema.
    with sqlite3.connect(str(db)) as conn:
        conn.execute(f"UPDATE alembic_version SET version_num = '{_PRIOR_REVISION}'")
        conn.commit()
    _upgrade(db)  # must NOT raise
    with sqlite3.connect(str(db)) as conn:
        assert _table_exists(conn, "hosts")
        assert _table_exists(conn, "models")
        assert _table_exists(conn, "model_benchmarks")


def test_alembic_version_rolls_back_one_revision(tmp_path: Path) -> None:
    db = tmp_path / "telemetry_downgrade.db"
    _upgrade(db)
    with sqlite3.connect(str(db)) as conn:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == (
            _TARGET_REVISION
        )
    _downgrade(db, "-1")
    with sqlite3.connect(str(db)) as conn:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == (
            _PRIOR_REVISION
        )
