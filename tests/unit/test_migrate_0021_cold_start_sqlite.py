"""SQLite-path tests for alembic revision 0021_benchmark_cold_start.

The stretch task of ``rfc-bench-embed-progress`` adds a nullable
``model_benchmarks.cold_start_s`` column so ``bench`` telemetry round-trips
into ``models list``. These tests pin, on the SQLite backend (the dialect
exercised at unit level — the Postgres path mirrors it and is covered by
the integration migration suite):

1. After ``migrate()``, ``model_benchmarks`` carries the ``cold_start_s``
   column.
2. ``migrate()`` is idempotent — a second run is a clean no-op (the
   ``PRAGMA``-guarded ``ADD COLUMN`` does not double-add or raise), like
   the rest of the 0015+ chain.
3. ``insert_model_benchmark(..., cold_start_s=X)`` round-trips through
   ``list_models_with_latest_benchmark()``.
4. The passive path (no ``cold_start_s`` arg) leaves the column ``NULL``.
"""

from __future__ import annotations

from pathlib import Path

from corpus_forge.backends.sqlite import SQLiteBackend


def _columns(backend: SQLiteBackend) -> set[str]:
    with backend._get_connection() as conn:
        rows = conn.execute("PRAGMA table_info(model_benchmarks)").fetchall()
    # PRAGMA table_info columns: (cid, name, type, notnull, dflt_value, pk)
    return {row[1] for row in rows}


def test_migrate_adds_cold_start_column(tmp_path: Path) -> None:
    backend = SQLiteBackend(path=str(tmp_path / "corpus.db"))
    backend.migrate()
    assert "cold_start_s" in _columns(backend), (
        "0021 must add model_benchmarks.cold_start_s on the SQLite path."
    )


def test_migrate_is_idempotent(tmp_path: Path) -> None:
    backend = SQLiteBackend(path=str(tmp_path / "corpus.db"))
    backend.migrate()
    # Second run must not raise (PRAGMA-guarded ADD COLUMN skips the re-add)
    # and must leave the column exactly once.
    backend.migrate()
    cols = _columns(backend)
    assert "cold_start_s" in cols


def _seed_model(backend: SQLiteBackend) -> None:
    backend.upsert_host(host_id="h1", hostname="mac", os="macOS", accelerator={"kind": "mps"})
    backend.upsert_models(
        [
            {
                "model_key": "st:m1",
                "kind": "embedder",
                "provider": "st",
                "model_id": "m1",
                "dimension": 384,
            }
        ]
    )


def test_cold_start_round_trips(tmp_path: Path) -> None:
    backend = SQLiteBackend(path=str(tmp_path / "corpus.db"))
    backend.migrate()
    _seed_model(backend)
    backend.insert_model_benchmark(
        host_id="h1",
        model_key="st:m1",
        source="bench",
        transport="local",
        device="mps",
        batch_size=32,
        sample_chunks=64,
        chunks_per_s=99.0,
        cold_start_s=1.25,
    )
    rows = backend.list_models_with_latest_benchmark()
    latest = [r for r in rows if r["host_id"] == "h1"]
    assert len(latest) == 1
    assert latest[0]["cold_start_s"] == 1.25


def test_passive_path_leaves_cold_start_null(tmp_path: Path) -> None:
    backend = SQLiteBackend(path=str(tmp_path / "corpus.db"))
    backend.migrate()
    _seed_model(backend)
    # The passive embed-run path never measures a discrete cold start.
    backend.insert_model_benchmark(
        host_id="h1",
        model_key="st:m1",
        source="embed-run",
        transport="local",
        device="mps",
        batch_size=32,
        sample_chunks=64,
        chunks_per_s=50.0,
    )
    rows = backend.list_models_with_latest_benchmark()
    latest = [r for r in rows if r["host_id"] == "h1"]
    assert len(latest) == 1
    assert latest[0]["cold_start_s"] is None
