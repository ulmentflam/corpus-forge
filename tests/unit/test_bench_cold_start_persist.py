"""RFC bench-embed-progress stretch — cold_start_s persistence round-trip.

Pins that ``insert_model_benchmark`` accepts and stores ``cold_start_s``,
that ``list_models_with_latest_benchmark`` surfaces it, and that the
``models list`` view layer (Rich table + agent-mode JSON) carries it.
"""

from __future__ import annotations

from pathlib import Path

from corpus_forge.admin.fleet_views import models_to_dict, render_models_table
from corpus_forge.backends.sqlite import SQLiteBackend


def _backend(tmp_path: Path) -> SQLiteBackend:
    backend = SQLiteBackend(path=str(tmp_path / "corpus.db"), schema="corpus")
    backend.migrate()
    return backend


def _seed(backend: SQLiteBackend) -> None:
    backend.upsert_host(host_id="h1", hostname="alpha", os="Linux", accelerator={"kind": "cuda"})
    backend.upsert_models(
        [
            {
                "model_key": "nomic-code",
                "kind": "code",
                "provider": "ollama",
                "model_id": "nomic-embed-code",
                "dimension": 768,
            }
        ]
    )


def test_insert_and_list_round_trips_cold_start(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    _seed(backend)

    backend.insert_model_benchmark(
        host_id="h1",
        model_key="nomic-code",
        source="bench",
        transport="local",
        device="cuda",
        batch_size=32,
        sample_chunks=64,
        chunks_per_s=120.0,
        cold_start_s=2.5,
    )

    rows = backend.list_models_with_latest_benchmark()
    nomic = [r for r in rows if r["model_key"] == "nomic-code"]
    assert nomic, "expected the seeded model in the listing"
    assert nomic[0]["cold_start_s"] == 2.5


def test_cold_start_defaults_to_null_when_omitted(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    _seed(backend)

    backend.insert_model_benchmark(
        host_id="h1",
        model_key="nomic-code",
        source="bench",
        transport="local",
        device="cuda",
        batch_size=32,
        sample_chunks=64,
        chunks_per_s=120.0,
    )

    rows = backend.list_models_with_latest_benchmark()
    nomic = [r for r in rows if r["model_key"] == "nomic-code"]
    assert nomic[0]["cold_start_s"] is None


def test_view_layer_surfaces_cold_start() -> None:
    rows = [
        {
            "model_key": "nomic-code",
            "kind": "code",
            "provider": "ollama",
            "model_id": "nomic-embed-code",
            "dimension": 768,
            "host_id": "h1",
            "chunks_per_s": 120.0,
            "cold_start_s": 2.5,
            "transport": "local",
            "device": "cuda",
            "source": "bench",
            "measured_at": None,
        }
    ]

    # Agent-mode JSON carries the key.
    payload = models_to_dict(rows)
    assert payload["models"][0]["cold_start_s"] == 2.5

    # Rich table renders without error and advertises the column header.
    table = render_models_table(rows)
    headers = [col.header for col in table.columns]
    assert any("cold start" in h for h in headers)
