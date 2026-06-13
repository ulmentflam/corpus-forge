"""Schema test for alembic revision 0021_benchmark_cold_start.

Verifies the nullable ``cold_start_s`` column added to
``model_benchmarks`` by
``corpus_forge/alembic/versions/0021_benchmark_cold_start.py`` lands with
the right type, the right NULL-ability, and idempotent re-run behaviour.

Backs the stretch task of RFC ``rfc-bench-embed-progress`` — ``bench``
persists ``cold_start_s`` and ``models list`` surfaces it, both of which
depend on this column existing.

Runs against SQLite in-memory only — Postgres-specific behaviour
(``ADD COLUMN IF NOT EXISTS``, schema prefix) is exercised by the
testcontainers lane of the broader alembic suite.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.integration


_REPO_ROOT = Path(__file__).resolve().parents[2]
_ALEMBIC_INI = _REPO_ROOT / "alembic.ini"
_REVISION_FILE = (
    _REPO_ROOT / "corpus_forge" / "alembic" / "versions" / "0021_benchmark_cold_start.py"
)
_TARGET = "0021_benchmark_cold_start"
_PRIOR = "0020_shared_config"


def _load_revision_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "_cold_start_migration_under_test", _REVISION_FILE
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _alembic_upgrade_sqlite(db_path: Path, target: str) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "corpus_forge" / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, target)


def _benchmarks_column_info(db_path: Path) -> dict[str, tuple[str, int]]:
    """Return ``{column_name: (type, notnull)}`` for ``model_benchmarks``."""
    import sqlite3

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("PRAGMA table_info(model_benchmarks)").fetchall()
    # (cid, name, type, notnull, dflt_value, pk)
    return {row[1]: (row[2].upper(), row[3]) for row in rows}


class TestRevisionMetadata:
    def test_revision_id_under_thirty_two_chars(self) -> None:
        mod = _load_revision_module()
        assert len(mod.revision) <= 32, (
            f"revision id {mod.revision!r} is {len(mod.revision)} chars; "
            "must fit in alembic_version.version_num VARCHAR(32)"
        )

    def test_down_revision_points_at_0020(self) -> None:
        mod = _load_revision_module()
        assert mod.down_revision == _PRIOR


class TestColdStartColumnSqlite:
    def test_column_present_and_nullable_after_upgrade(self, tmp_path: Path) -> None:
        db = tmp_path / "corpus.db"
        _alembic_upgrade_sqlite(db, _TARGET)

        cols = _benchmarks_column_info(db)
        assert "cold_start_s" in cols, (
            f"cold_start_s missing from model_benchmarks; present: {sorted(cols)}"
        )
        actual_type, notnull = cols["cold_start_s"]
        assert actual_type == "REAL", f"cold_start_s type is {actual_type!r}, expected REAL"
        assert notnull == 0, "cold_start_s must be nullable so existing rows need no backfill"

    def test_pre_existing_columns_unchanged(self, tmp_path: Path) -> None:
        db = tmp_path / "corpus.db"
        _alembic_upgrade_sqlite(db, _TARGET)

        cols = _benchmarks_column_info(db)
        required = {
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
        missing = required - set(cols)
        assert not missing, f"pre-existing model_benchmarks columns dropped by 0021: {missing}"

    def test_upgrade_is_idempotent_against_already_migrated_db(self, tmp_path: Path) -> None:
        """Re-running the 0021 upgrade against a DB that already has the
        column must not raise — the SQLite path probes PRAGMA table_info
        before ALTER TABLE. Force the probe by rolling alembic_version
        back to 0020 and re-running.
        """
        import sqlite3

        db = tmp_path / "corpus.db"
        _alembic_upgrade_sqlite(db, _TARGET)

        with sqlite3.connect(db) as conn:
            row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        assert row[0] == _TARGET

        with sqlite3.connect(db) as conn:
            conn.execute("UPDATE alembic_version SET version_num = ?", (_PRIOR,))
            conn.commit()

        # Should not raise (duplicate column would, without the probe).
        _alembic_upgrade_sqlite(db, _TARGET)

        cols = _benchmarks_column_info(db)
        assert "cold_start_s" in cols, "cold_start_s lost on idempotent re-run"
