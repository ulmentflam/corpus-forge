"""Regression tests for ``audit_embedder_drift`` + ``reconcile_embedder_drift``.

These pin the helper functions that back the new
``corpus-forge embedder gc`` CLI command and the
``embedder_drift`` doctor check.

What we pin
-----------
1. ``audit_embedder_drift`` returns ``[]`` on SQLite backends — that
   class never trips the drift bug in practice (single-host installs
   rarely rename embedders).
2. The audit walks ``corpus.embedders`` and filters by names NOT in
   ``cfg.embedders``.
3. Missing per-embedder table is recorded as
   ``table_exists=False`` rather than crashing the audit.
4. Best-effort size/count probes degrade to ``None`` on errors
   instead of erroring out.
5. ``reconcile_embedder_drift`` issues both the
   ``DROP TABLE IF EXISTS … CASCADE`` AND the ``DELETE FROM
   corpus.embedders WHERE id = …`` per orphan.
6. A partially-cleaned orphan (table already dropped) still gets
   its catalog row deleted — no extra DROP attempt.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from corpus_forge.admin.embedder import (
    EmbedderDriftRow,
    audit_embedder_drift,
    reconcile_embedder_drift,
)

# ─────────────────────────────────────────────────────────────────────
# Test doubles
# ─────────────────────────────────────────────────────────────────────


class FakeBackend:
    """Deterministic stand-in for ``PostgresBackend``.

    ``_execute`` matches on the query prefix and returns a canned row
    list. The fixture lets each test seed exactly the rows it needs.
    Each call's ``(query, params)`` tuple is appended to
    :attr:`calls` so reconcile tests can assert what SQL ran.
    """

    def __init__(self) -> None:
        self.embedders_rows: list[dict] = []
        self.table_exists_rows: dict[str, list[dict]] = {}
        self.size_rows: dict[str, list[dict]] = {}
        self.count_rows: dict[str, list[dict]] = {}
        self.calls: list[tuple[str, tuple]] = []
        # When set, raises on the matching prefix to simulate DB errors.
        self.size_error_for: set[str] = set()
        self.count_error_for: set[str] = set()

    def _execute(self, query: str, params: tuple = ()) -> list[dict]:
        self.calls.append((query, params))
        q = query.strip().lower()
        if q.startswith("select id, name, dimension, table_name"):
            return self.embedders_rows
        if q.startswith("select 1 from information_schema.tables"):
            (table_name,) = params
            return self.table_exists_rows.get(table_name, [])
        if q.startswith("select pg_total_relation_size"):
            (table_name,) = params
            if table_name in self.size_error_for:
                raise RuntimeError("simulated pg_total_relation_size failure")
            return self.size_rows.get(table_name, [])
        if q.startswith("select count(*) as n from corpus."):
            table_name = q.split('corpus."')[1].split('"', 1)[0]
            if table_name in self.count_error_for:
                raise RuntimeError("simulated count failure")
            return self.count_rows.get(table_name, [])
        # DDL operations: DROP TABLE / DELETE FROM
        return []


class SQLiteBackend:
    """Marker class — ``audit_embedder_drift`` short-circuits on this name."""


def _cfg(embedder_names: list[str]) -> MagicMock:
    cfg = MagicMock()
    cfg.embedders = [MagicMock(name=n) for n in embedder_names]
    # Sidestep ``MagicMock.name`` being a special attribute by re-assigning.
    for embedder_obj, n in zip(cfg.embedders, embedder_names, strict=True):
        embedder_obj.name = n
    return cfg


# ─────────────────────────────────────────────────────────────────────
# audit_embedder_drift
# ─────────────────────────────────────────────────────────────────────


class TestAuditSqliteShortCircuit:
    def test_sqlite_backend_returns_empty_list(self) -> None:
        result = audit_embedder_drift(SQLiteBackend(), _cfg(["whatever"]))
        assert result == []


class TestAuditDetection:
    def test_no_orphans_when_db_matches_config(self) -> None:
        backend = FakeBackend()
        backend.embedders_rows = [
            {
                "id": 1,
                "name": "qwen3-4096",
                "dimension": 4096,
                "table_name": "embeddings_qwen3_4096",
            },
        ]
        result = audit_embedder_drift(backend, _cfg(["qwen3-4096"]))
        assert result == []

    def test_finds_single_orphan_renamed_embedder(self) -> None:
        """The exact bug shape from 2026-05-22: ``qwen3-2000`` renamed away."""

        backend = FakeBackend()
        backend.embedders_rows = [
            {
                "id": 2,
                "name": "qwen3-2000",
                "dimension": 2000,
                "table_name": "embeddings_qwen3_2000",
            },
            {
                "id": 5,
                "name": "qwen3-4096",
                "dimension": 4096,
                "table_name": "embeddings_qwen3_4096",
            },
        ]
        backend.table_exists_rows["embeddings_qwen3_2000"] = [{"?column?": 1}]
        backend.size_rows["embeddings_qwen3_2000"] = [{"bytes": 219_103_232}]
        backend.count_rows["embeddings_qwen3_2000"] = [{"n": 8976}]

        result = audit_embedder_drift(backend, _cfg(["qwen3-4096"]))
        assert len(result) == 1
        orphan = result[0]
        assert orphan.name == "qwen3-2000"
        assert orphan.db_id == 2
        assert orphan.dimension == 2000
        assert orphan.table_name == "embeddings_qwen3_2000"
        assert orphan.table_exists is True
        assert orphan.table_size_bytes == 219_103_232
        assert orphan.row_count == 8976

    def test_finds_multiple_orphans(self) -> None:
        backend = FakeBackend()
        backend.embedders_rows = [
            {"id": 2, "name": "old-a", "dimension": 1024, "table_name": "embeddings_old_a"},
            {"id": 3, "name": "old-b", "dimension": 768, "table_name": "embeddings_old_b"},
            {"id": 5, "name": "current", "dimension": 4096, "table_name": "embeddings_current"},
        ]
        backend.table_exists_rows["embeddings_old_a"] = [{"?column?": 1}]
        backend.table_exists_rows["embeddings_old_b"] = [{"?column?": 1}]
        backend.size_rows["embeddings_old_a"] = [{"bytes": 100_000_000}]
        backend.size_rows["embeddings_old_b"] = [{"bytes": 50_000_000}]
        backend.count_rows["embeddings_old_a"] = [{"n": 4000}]
        backend.count_rows["embeddings_old_b"] = [{"n": 2000}]

        result = audit_embedder_drift(backend, _cfg(["current"]))
        assert {o.name for o in result} == {"old-a", "old-b"}
        assert {o.row_count for o in result} == {4000, 2000}


class TestAuditMissingTable:
    def test_orphan_with_missing_table_records_table_exists_false(self) -> None:
        """Catalog row exists but per-embedder table was already dropped."""

        backend = FakeBackend()
        backend.embedders_rows = [
            {"id": 9, "name": "ghost", "dimension": 768, "table_name": "embeddings_ghost"},
        ]
        # No entry in table_exists_rows → existence probe returns []
        result = audit_embedder_drift(backend, _cfg(["other"]))
        assert len(result) == 1
        orphan = result[0]
        assert orphan.name == "ghost"
        assert orphan.table_exists is False
        assert orphan.table_size_bytes is None
        assert orphan.row_count is None

    def test_orphan_with_empty_table_name_skips_table_probes(self) -> None:
        """Misconfigured ``embedders`` row with no ``table_name``."""

        backend = FakeBackend()
        backend.embedders_rows = [
            {"id": 11, "name": "weird", "dimension": 1024, "table_name": None},
        ]
        result = audit_embedder_drift(backend, _cfg(["other"]))
        assert len(result) == 1
        orphan = result[0]
        assert orphan.table_name == ""
        assert orphan.table_exists is False


class TestAuditErrorTolerance:
    def test_size_probe_failure_degrades_to_none(self) -> None:
        """``pg_total_relation_size`` errors should NOT crash the audit."""

        backend = FakeBackend()
        backend.embedders_rows = [
            {"id": 2, "name": "orphan", "dimension": 2000, "table_name": "embeddings_orphan"},
        ]
        backend.table_exists_rows["embeddings_orphan"] = [{"?column?": 1}]
        backend.size_error_for.add("embeddings_orphan")
        backend.count_rows["embeddings_orphan"] = [{"n": 100}]

        result = audit_embedder_drift(backend, _cfg(["other"]))
        assert result[0].table_size_bytes is None
        # Row count should still succeed.
        assert result[0].row_count == 100

    def test_count_probe_failure_degrades_to_none(self) -> None:
        backend = FakeBackend()
        backend.embedders_rows = [
            {"id": 2, "name": "orphan", "dimension": 2000, "table_name": "embeddings_orphan"},
        ]
        backend.table_exists_rows["embeddings_orphan"] = [{"?column?": 1}]
        backend.size_rows["embeddings_orphan"] = [{"bytes": 100_000}]
        backend.count_error_for.add("embeddings_orphan")

        result = audit_embedder_drift(backend, _cfg(["other"]))
        assert result[0].table_size_bytes == 100_000
        assert result[0].row_count is None


# ─────────────────────────────────────────────────────────────────────
# reconcile_embedder_drift
# ─────────────────────────────────────────────────────────────────────


class TestReconcile:
    def test_drops_table_and_deletes_row(self) -> None:
        backend = FakeBackend()
        orphan = EmbedderDriftRow(
            name="qwen3-2000",
            db_id=2,
            dimension=2000,
            table_name="embeddings_qwen3_2000",
            table_exists=True,
            table_size_bytes=219_103_232,
            row_count=8976,
        )
        reconcile_embedder_drift(backend, [orphan])

        queries = [c[0].lower().strip() for c in backend.calls]
        assert any('drop table if exists corpus."embeddings_qwen3_2000"' in q for q in queries)
        assert any("delete from corpus.embedders where id = %s" in q for q in queries)

        delete_calls = [c for c in backend.calls if "delete from" in c[0].lower()]
        assert delete_calls
        assert delete_calls[0][1] == (2,)

    def test_skips_drop_when_table_already_missing(self) -> None:
        """Partially-cleaned orphan: no extra DROP attempt; row delete still fires."""

        backend = FakeBackend()
        orphan = EmbedderDriftRow(
            name="ghost",
            db_id=7,
            dimension=768,
            table_name="embeddings_ghost",
            table_exists=False,
            table_size_bytes=None,
            row_count=None,
        )
        reconcile_embedder_drift(backend, [orphan])

        queries = [c[0].lower().strip() for c in backend.calls]
        assert not any("drop table" in q for q in queries)
        assert any("delete from corpus.embedders where id = %s" in q for q in queries)

    def test_empty_orphan_list_is_noop(self) -> None:
        backend = FakeBackend()
        reconcile_embedder_drift(backend, [])
        assert backend.calls == []

    def test_processes_multiple_orphans(self) -> None:
        backend = FakeBackend()
        orphans = [
            EmbedderDriftRow(
                name=f"old-{i}",
                db_id=i,
                dimension=1024,
                table_name=f"embeddings_old_{i}",
                table_exists=True,
                table_size_bytes=100,
                row_count=10,
            )
            for i in range(2, 5)
        ]
        reconcile_embedder_drift(backend, orphans)

        drops = [c for c in backend.calls if "drop table" in c[0].lower()]
        deletes = [c for c in backend.calls if "delete from" in c[0].lower()]
        assert len(drops) == 3
        assert len(deletes) == 3
        assert {d[1] for d in deletes} == {(2,), (3,), (4,)}


# ─────────────────────────────────────────────────────────────────────
# Module-level surface
# ─────────────────────────────────────────────────────────────────────


def test_public_surface_includes_new_names() -> None:
    """``__all__`` must export the new helpers so the doctor module
    can import them via the public path."""

    from corpus_forge.admin import embedder as embedder_mod

    assert "EmbedderDriftRow" in embedder_mod.__all__
    assert "audit_embedder_drift" in embedder_mod.__all__
    assert "reconcile_embedder_drift" in embedder_mod.__all__


@pytest.mark.parametrize(
    ("byte_count", "expected_unit"),
    [
        (0, "—"),
        (None, "—"),
        (-1, "—"),
        (500, "B"),
        (2 * 1024, "KB"),
        (3 * 1024 * 1024, "MB"),
        (5 * 1024 * 1024 * 1024, "GB"),
    ],
)
def test_human_bytes_units(byte_count: int | None, expected_unit: str) -> None:
    """Pin the size-rendering helper used by the CLI table."""

    from corpus_forge.admin.embedder import _human_bytes

    assert expected_unit in _human_bytes(byte_count)
