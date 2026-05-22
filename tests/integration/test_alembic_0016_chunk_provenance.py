"""Schema test for alembic revision 0016_chunk_provenance.

Verifies the five nullable provenance columns added to the ``chunks``
table by ``corpus_forge/alembic/versions/0016_chunk_provenance.py``
land with the right types, the right NULL-ability, and the right
idempotence properties.

Backs the first task of RFC ``rfc-source-provenance-git-and-lines``
(P0). Subsequent RFC tasks (chunker write paths, MCP read tool) all
depend on these columns existing, so the contract is pinned here in
a tightly-scoped schema test rather than discovered downstream when
a future migration breaks.

Runs against SQLite in-memory only — Postgres-specific behaviour
(``ADD COLUMN IF NOT EXISTS``, schema prefix) is exercised by the
testcontainers lane of the broader alembic test suite. This file
intentionally has no postgres dependency to keep the unit/CI tier
fast.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.integration


_REPO_ROOT = Path(__file__).resolve().parents[2]
_ALEMBIC_INI = _REPO_ROOT / "alembic.ini"
_REVISION_FILE = _REPO_ROOT / "corpus_forge" / "alembic" / "versions" / "0016_chunk_provenance.py"


def _load_revision_module() -> ModuleType:
    """Load the 0016 migration module by file path.

    Python won't ``import`` a module whose name starts with a digit, so
    we use ``importlib.util.spec_from_file_location`` (the same shape
    alembic itself uses to load revisions).
    """
    spec = importlib.util.spec_from_file_location(
        "_chunk_provenance_migration_under_test", _REVISION_FILE
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _alembic_upgrade_sqlite(db_path: Path, target: str) -> None:
    """Run ``alembic upgrade <target>`` against a SQLite *db_path*."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option(
        "script_location",
        str(_REPO_ROOT / "corpus_forge" / "alembic"),
    )
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, target)


def _chunks_column_info(db_path: Path) -> dict[str, tuple[str, int]]:
    """Return ``{column_name: (type, notnull)}`` for the ``chunks`` table.

    ``notnull`` is 1 for NOT NULL, 0 for nullable — matches SQLite's
    ``PRAGMA table_info`` output.
    """
    import sqlite3

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("PRAGMA table_info(chunks)").fetchall()
    # (cid, name, type, notnull, dflt_value, pk)
    return {row[1]: (row[2].upper(), row[3]) for row in rows}


class TestRevisionMetadata:
    """Pin the revision id length + the upgrade chain stays linear."""

    def test_revision_id_under_thirty_two_chars(self) -> None:
        """Alembic's default ``alembic_version.version_num`` is VARCHAR(32).

        See the long note on revision 0015 — overlong ids trip
        ``StringDataRightTruncation`` and roll back the whole
        migration on Postgres. Catch it locally.
        """
        mod = _load_revision_module()

        assert len(mod.revision) <= 32, (
            f"revision id {mod.revision!r} is {len(mod.revision)} chars; "
            "must fit in alembic_version.version_num VARCHAR(32) — see "
            "the long note on revision 0015 for the regression context"
        )

    def test_down_revision_points_at_0015(self) -> None:
        """Forward-only chain stays linear — 0016 builds directly on 0015."""
        mod = _load_revision_module()

        assert mod.down_revision == "0015_halfvec_hnsw_index"


class TestChunkProvenanceColumnsSqlite:
    """Apply 0016 against fresh SQLite; assert every new column lands."""

    def test_all_five_columns_present_after_upgrade(self, tmp_path: Path) -> None:
        db = tmp_path / "corpus.db"
        _alembic_upgrade_sqlite(db, "0016_chunk_provenance")

        cols = _chunks_column_info(db)
        expected = {
            "file_path": "TEXT",
            "line_start": "INTEGER",
            "line_end": "INTEGER",
            "git_commit": "TEXT",
            "git_branch": "TEXT",
        }
        for name, type_ in expected.items():
            assert name in cols, (
                f"column {name!r} missing from chunks after upgrade; "
                f"present columns: {sorted(cols)}"
            )
            actual_type, notnull = cols[name]
            assert actual_type == type_, (
                f"column {name!r} has type {actual_type!r}, expected {type_!r}"
            )
            assert notnull == 0, (
                f"column {name!r} is NOT NULL — RFC requires nullable so "
                "existing rows don't need backfill"
            )

    def test_pre_existing_columns_unchanged(self, tmp_path: Path) -> None:
        """The 0016 migration must not touch the other chunk columns.

        Pre-existing schema (from 0001 + later revisions) provides
        ``id``, ``document_id``, ``conversation_id``, ``message_id``,
        ``chunk_index``, ``text``, ``heading``, ``role``,
        ``token_count``, ``metadata``. If any of these get dropped or
        renamed by 0016 (or any later additive migration silently
        masks them), this test catches it.
        """
        db = tmp_path / "corpus.db"
        _alembic_upgrade_sqlite(db, "0016_chunk_provenance")

        cols = _chunks_column_info(db)
        required_pre_existing = {
            "id",
            "document_id",
            "conversation_id",
            "message_id",
            "chunk_index",
            "text",
            "heading",
            "role",
            "token_count",
            "metadata",
        }
        missing = required_pre_existing - set(cols)
        assert not missing, f"pre-existing chunks columns dropped by 0016: {missing}"

    def test_upgrade_is_idempotent_against_already_migrated_db(self, tmp_path: Path) -> None:
        """Running upgrade twice must not raise (forward-only + idempotent).

        SQLite has no ``ADD COLUMN IF NOT EXISTS``; the migration probes
        ``PRAGMA table_info`` first. This test pins that the probe
        actually fires by exercising a double-upgrade — without the
        probe, the second pass would raise ``duplicate column name``.
        """
        db = tmp_path / "corpus.db"
        _alembic_upgrade_sqlite(db, "0016_chunk_provenance")

        # Run upgrade again to the same target — should be a no-op,
        # NOT raise. Alembic itself recognises "already at target" and
        # short-circuits before invoking the migration's upgrade()
        # function, so to actually exercise the column-probe path we
        # have to manually call upgrade() with the env already at the
        # target revision.
        import sqlite3

        with sqlite3.connect(db) as conn:
            # Sanity: confirm we're already at 0016.
            row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        assert row[0] == "0016_chunk_provenance"

        # Roll the alembic_version back to 0015 then re-run upgrade —
        # this forces the 0016 upgrade() function to execute against
        # a DB that already has the columns.
        with sqlite3.connect(db) as conn:
            conn.execute("UPDATE alembic_version SET version_num = '0015_halfvec_hnsw_index'")
            conn.commit()

        # Should not raise.
        _alembic_upgrade_sqlite(db, "0016_chunk_provenance")

        # And the columns should still be there exactly once.
        cols = _chunks_column_info(db)
        for name in ("file_path", "line_start", "line_end", "git_commit", "git_branch"):
            assert name in cols, f"column {name} lost on idempotent re-run"


class TestMigrationDocstring:
    """The migration's docstring carries the RFC cross-reference."""

    def test_docstring_names_rfc(self) -> None:
        """Future maintainers can grep for ``rfc-source-provenance`` and find this."""
        mod = _load_revision_module()

        doc = (mod.__doc__ or "").lower()
        assert "rfc-source-provenance" in doc or "source-provenance" in doc, (
            "migration docstring must name the RFC for grep-discovery"
        )
