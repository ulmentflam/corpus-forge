"""Unit tests for the fleet telemetry heartbeat (rfc-fleet-1).

Two layers are exercised:

1. :mod:`corpus_forge.telemetry_registry` — the failure-isolated glue:
   accelerator serialisation, embedder→model row shaping, the
   ``ollama list`` best-effort path, and the contract that NO failure
   ever propagates to the caller.
2. The :class:`SQLiteBackend` ``upsert_host`` / ``upsert_models``
   implementations — UPSERT/idempotency semantics against a real
   in-memory-backed (tmp_path) SQLite DB.

Postgres parity for the backend methods is covered by the integration
suite; the SQL shape is byte-aligned between the two backends.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from corpus_forge import telemetry_registry as tr
from corpus_forge.acceleration import Accelerator, AcceleratorInfo
from corpus_forge.admin.ollama import OllamaModel
from corpus_forge.backends.sqlite import SQLiteBackend

# ---------------------------------------------------------------------------
# Fixtures / stubs
# ---------------------------------------------------------------------------


class _StubEmbedder:
    """Minimal stand-in for an EmbedderConfig (only the read fields)."""

    def __init__(self, provider: str, model_id: str, dimension: int) -> None:
        self.provider = provider
        self.model_id = model_id
        self.dimension = dimension


class _StubConfig:
    """Minimal Config surface used by the telemetry helpers."""

    def __init__(self, embedders: list[_StubEmbedder], host_id: str = "host-xyz") -> None:
        self.embedders = embedders
        self._host_id = host_id

    def host_id(self) -> str:
        return self._host_id


class _RecordingBackend:
    """Backend double that records upsert calls (no DB)."""

    def __init__(self) -> None:
        self.host_calls: list[dict] = []
        self.model_calls: list[list[dict]] = []

    def upsert_host(self, **kwargs: Any) -> None:
        self.host_calls.append(kwargs)

    def upsert_models(self, rows: list[dict]) -> None:
        self.model_calls.append(rows)


class _RaisingBackend:
    """Backend double whose every method raises — failure-isolation probe."""

    def upsert_host(self, **kwargs: Any) -> None:
        raise RuntimeError("backend unreachable")

    def upsert_models(self, rows: list[dict]) -> None:
        raise RuntimeError("backend unreachable")


@pytest.fixture
def cfg() -> _StubConfig:
    return _StubConfig(
        embedders=[
            _StubEmbedder("llama-cpp", "qwen3-embedding:8b", 4096),
            _StubEmbedder("openai", "text-embedding-3-small", 1536),
        ]
    )


def _backend(tmp_path: Path) -> SQLiteBackend:
    backend = SQLiteBackend(path=str(tmp_path / "corpus.db"), schema="corpus")
    backend.migrate()
    return backend


# ---------------------------------------------------------------------------
# accelerator_payload
# ---------------------------------------------------------------------------


def test_accelerator_payload_is_json_serialisable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        tr,
        "detect_accelerator",
        lambda: AcceleratorInfo(kind=Accelerator.CUDA, device_name="RTX 4090", vram_mb=24576),
    )
    payload = tr.accelerator_payload()
    # kind must be a plain string, not the StrEnum, and the whole dict
    # must round-trip through json.
    assert payload["kind"] == "cuda"
    assert isinstance(payload["kind"], str)
    assert payload["device_name"] == "RTX 4090"
    assert payload["vram_mb"] == 24576
    assert json.loads(json.dumps(payload)) == payload


def test_accelerator_payload_cpu_lane_has_null_optionals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tr, "detect_accelerator", lambda: AcceleratorInfo(kind=Accelerator.CPU))
    payload = tr.accelerator_payload()
    assert payload == {"kind": "cpu", "device_name": None, "vram_mb": None}


# ---------------------------------------------------------------------------
# embedder → model rows
# ---------------------------------------------------------------------------


def test_embedder_model_rows_shape(cfg: _StubConfig) -> None:
    rows = tr._embedder_model_rows(cfg)
    assert rows == [
        {
            "model_key": "llama-cpp:qwen3-embedding:8b",
            "kind": "embedder",
            "provider": "llama-cpp",
            "model_id": "qwen3-embedding:8b",
            "dimension": 4096,
        },
        {
            "model_key": "openai:text-embedding-3-small",
            "kind": "embedder",
            "provider": "openai",
            "model_id": "text-embedding-3-small",
            "dimension": 1536,
        },
    ]


# ---------------------------------------------------------------------------
# ollama kind inference + best-effort probe
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("signals", "expected"),
    [
        (("nomic-embed-text", "nomic-bert"), "embedder"),
        (("bge-m3", ""), "embedder"),
        (("all-minilm", "bert"), "embedder"),
        (("llama3.1:8b", "llama"), "llm"),
        (("qwen2.5:7b", "qwen2"), "llm"),
        (("", ""), "llm"),
    ],
)
def test_infer_ollama_kind(signals: tuple[str, ...], expected: str) -> None:
    assert tr._infer_ollama_kind(*signals) == expected


def test_ollama_model_rows_maps_tags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "corpus_forge.admin.ollama.fetch_tags",
        lambda timeout=2.0: [
            OllamaModel(name="nomic-embed-text", size=1, modified_at="t", family="nomic-bert"),
            OllamaModel(name="llama3.1:8b", size=2, modified_at="t", family="llama"),
        ],
    )
    rows = tr._ollama_model_rows()
    assert rows == [
        {
            "model_key": "ollama:nomic-embed-text",
            "kind": "embedder",
            "provider": "ollama",
            "model_id": "nomic-embed-text",
            "dimension": None,
        },
        {
            "model_key": "ollama:llama3.1:8b",
            "kind": "llm",
            "provider": "ollama",
            "model_id": "llama3.1:8b",
            "dimension": None,
        },
    ]


def test_ollama_model_rows_absent_daemon_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing ``fetch_tags`` (no daemon) yields an empty list, not an error."""

    def _boom(timeout: float = 2.0) -> list:
        raise OSError("connection refused")

    monkeypatch.setattr("corpus_forge.admin.ollama.fetch_tags", _boom)
    assert tr._ollama_model_rows() == []


# ---------------------------------------------------------------------------
# record_host_heartbeat / record_model_registry — success + isolation
# ---------------------------------------------------------------------------


def test_record_host_heartbeat_passes_probe_payload(
    monkeypatch: pytest.MonkeyPatch, cfg: _StubConfig
) -> None:
    monkeypatch.setattr(tr, "detect_accelerator", lambda: AcceleratorInfo(kind=Accelerator.MPS))
    backend = _RecordingBackend()
    tr.record_host_heartbeat(backend, cfg)
    assert len(backend.host_calls) == 1
    call = backend.host_calls[0]
    assert call["host_id"] == "host-xyz"
    assert call["accelerator"]["kind"] == "mps"
    assert isinstance(call["hostname"], str) and call["hostname"]
    assert isinstance(call["os"], str) and call["os"]


def test_record_host_heartbeat_isolates_backend_failure(cfg: _StubConfig) -> None:
    """A raising backend must NOT propagate out of record_host_heartbeat."""
    tr.record_host_heartbeat(_RaisingBackend(), cfg)  # no raise == pass


def test_record_model_registry_combines_embedders_and_ollama(
    monkeypatch: pytest.MonkeyPatch, cfg: _StubConfig
) -> None:
    monkeypatch.setattr(
        "corpus_forge.admin.ollama.fetch_tags",
        lambda timeout=2.0: [
            OllamaModel(name="llama3.1:8b", size=2, modified_at="t", family="llama")
        ],
    )
    backend = _RecordingBackend()
    tr.record_model_registry(backend, cfg)
    assert len(backend.model_calls) == 1
    keys = [r["model_key"] for r in backend.model_calls[0]]
    assert keys == [
        "llama-cpp:qwen3-embedding:8b",
        "openai:text-embedding-3-small",
        "ollama:llama3.1:8b",
    ]


def test_record_model_registry_isolates_backend_failure(
    monkeypatch: pytest.MonkeyPatch, cfg: _StubConfig
) -> None:
    monkeypatch.setattr("corpus_forge.admin.ollama.fetch_tags", lambda timeout=2.0: [])
    tr.record_model_registry(_RaisingBackend(), cfg)  # no raise == pass


# ---------------------------------------------------------------------------
# heartbeat entry point
# ---------------------------------------------------------------------------


def test_heartbeat_none_backend_is_noop(cfg: _StubConfig) -> None:
    tr.heartbeat(None, cfg)  # must not raise


def test_heartbeat_records_host_then_models(
    monkeypatch: pytest.MonkeyPatch, cfg: _StubConfig
) -> None:
    monkeypatch.setattr(tr, "detect_accelerator", lambda: AcceleratorInfo(kind=Accelerator.CPU))
    monkeypatch.setattr("corpus_forge.admin.ollama.fetch_tags", lambda timeout=2.0: [])
    backend = _RecordingBackend()
    tr.heartbeat(backend, cfg)
    assert len(backend.host_calls) == 1
    assert len(backend.model_calls) == 1


# ---------------------------------------------------------------------------
# SQLiteBackend.upsert_host / upsert_models — real DB
# ---------------------------------------------------------------------------


def test_sqlite_upsert_host_inserts_and_updates(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    backend.upsert_host(
        host_id="h1",
        hostname="alpha",
        os="Linux-x",
        accelerator={"kind": "cuda", "vram_mb": 24576},
    )
    rows = backend._execute("SELECT * FROM hosts WHERE host_id = ?", ("h1",))
    assert len(rows) == 1
    assert rows[0]["hostname"] == "alpha"
    # accelerator stored as JSON TEXT — round-trips.
    assert json.loads(rows[0]["accelerator"]) == {"kind": "cuda", "vram_mb": 24576}
    first_seen = rows[0]["last_seen"]
    assert first_seen

    # Re-upsert updates the row in place (no duplicate, new hostname).
    backend.upsert_host(
        host_id="h1",
        hostname="alpha-renamed",
        os="Linux-y",
        accelerator={"kind": "cpu"},
    )
    rows = backend._execute("SELECT * FROM hosts WHERE host_id = ?", ("h1",))
    assert len(rows) == 1
    assert rows[0]["hostname"] == "alpha-renamed"
    assert json.loads(rows[0]["accelerator"]) == {"kind": "cpu"}


def test_sqlite_upsert_host_null_accelerator(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    backend.upsert_host(host_id="h2", hostname="b", os="o", accelerator=None)
    rows = backend._execute("SELECT accelerator FROM hosts WHERE host_id = ?", ("h2",))
    assert rows[0]["accelerator"] is None


def test_sqlite_upsert_host_preserves_tailscale_name_on_update(tmp_path: Path) -> None:
    """A later upsert without a tailscale_name must not clobber an existing one."""
    backend = _backend(tmp_path)
    backend.upsert_host(
        host_id="h3",
        hostname="c",
        os="o",
        accelerator=None,
        tailscale_name="c.tailnet.ts.net",
    )
    backend.upsert_host(host_id="h3", hostname="c2", os="o2", accelerator=None)
    rows = backend._execute("SELECT tailscale_name FROM hosts WHERE host_id = ?", ("h3",))
    assert rows[0]["tailscale_name"] == "c.tailnet.ts.net"


def test_sqlite_upsert_models_inserts_and_preserves_first_seen(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
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
    rows = backend._execute("SELECT * FROM models WHERE model_key = ?", ("openai:m",))
    assert len(rows) == 1
    original_first_seen = rows[0]["first_seen"]
    assert rows[0]["dimension"] == 1536

    # Re-insert with a different dimension: ON CONFLICT DO NOTHING means
    # the original row (and first_seen) survives untouched.
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
    rows = backend._execute("SELECT * FROM models WHERE model_key = ?", ("openai:m",))
    assert len(rows) == 1
    assert rows[0]["dimension"] == 1536
    assert rows[0]["first_seen"] == original_first_seen


def test_sqlite_upsert_models_null_dimension(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    backend.upsert_models(
        [{"model_key": "ollama:llama", "kind": "llm", "provider": "ollama", "model_id": "llama"}]
    )
    rows = backend._execute("SELECT dimension FROM models WHERE model_key = ?", ("ollama:llama",))
    assert rows[0]["dimension"] is None


def test_sqlite_upsert_models_empty_is_noop(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    backend.upsert_models([])
    rows = backend._execute("SELECT COUNT(*) AS n FROM models", ())
    assert rows[0]["n"] == 0


def test_sqlite_benchmark_fk_chain(tmp_path: Path) -> None:
    """A model_benchmarks row can reference an upserted host + model."""
    backend = _backend(tmp_path)
    backend.upsert_host(host_id="h", hostname="x", os="o", accelerator=None)
    backend.upsert_models(
        [{"model_key": "openai:m", "kind": "embedder", "provider": "openai", "model_id": "m"}]
    )
    backend._execute(
        "INSERT INTO model_benchmarks "
        "(host_id, model_key, source, transport, device, batch_size, sample_chunks, "
        " chunks_per_s, measured_at) "
        "VALUES (?, ?, 'bench', 'local', 'cpu', 8, 64, 12.5, ?)",
        ("h", "openai:m", "2026-06-05T00:00:00Z"),
    )
    rows = backend._execute("SELECT chunks_per_s FROM model_benchmarks", ())
    assert rows[0]["chunks_per_s"] == pytest.approx(12.5)


def test_sqlite_upsert_models_swallows_nothing_unexpected(tmp_path: Path) -> None:
    """A missing required model_key surfaces as KeyError (programmer error, not silent)."""
    backend = _backend(tmp_path)
    with pytest.raises(KeyError):
        backend.upsert_models([{"provider": "openai", "model_id": "m"}])


def test_module_uses_sqlite_connect_marker() -> None:
    """Guard import so a refactor that drops sqlite3 from this test is obvious."""
    assert sqlite3.sqlite_version  # trivial smoke that sqlite3 is importable


# ---------------------------------------------------------------------------
# insert_model_benchmark backend method (rfc-fleet-1 item 4)
# ---------------------------------------------------------------------------


def test_sqlite_insert_model_benchmark_full_row(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    backend.upsert_host(host_id="h", hostname="x", os="o", accelerator=None)
    backend.upsert_models(
        [{"model_key": "openai:m", "kind": "embedder", "provider": "openai", "model_id": "m"}]
    )
    backend.insert_model_benchmark(
        host_id="h",
        model_key="openai:m",
        source="bench",
        transport="api",
        device="remote",
        batch_size=32,
        sample_chunks=64,
        chunks_per_s=42.0,
        tokens_per_s=1000.0,
        latency_p50_ms=10.0,
        latency_p95_ms=25.0,
    )
    rows = backend._execute("SELECT * FROM model_benchmarks", ())
    assert len(rows) == 1
    row = rows[0]
    assert row["source"] == "bench"
    assert row["transport"] == "api"
    assert row["device"] == "remote"
    assert row["chunks_per_s"] == pytest.approx(42.0)
    assert row["tokens_per_s"] == pytest.approx(1000.0)
    assert row["latency_p50_ms"] == pytest.approx(10.0)
    assert row["measured_at"]


def test_sqlite_insert_model_benchmark_optional_columns_default_none(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    backend.upsert_host(host_id="h", hostname="x", os="o", accelerator=None)
    backend.upsert_models(
        [{"model_key": "st:e", "kind": "embedder", "provider": "st", "model_id": "e"}]
    )
    # embed-run shape: no latencies / tokens.
    backend.insert_model_benchmark(
        host_id="h",
        model_key="st:e",
        source="embed-run",
        transport="local",
        device="cpu",
        batch_size=None,
        sample_chunks=10000,
        chunks_per_s=5.5,
    )
    rows = backend._execute("SELECT * FROM model_benchmarks", ())
    assert rows[0]["source"] == "embed-run"
    assert rows[0]["tokens_per_s"] is None
    assert rows[0]["latency_p50_ms"] is None
    assert rows[0]["batch_size"] is None


def test_sqlite_insert_model_benchmark_appends(tmp_path: Path) -> None:
    """Append-only — two inserts for the same (host, model) keep both rows."""
    backend = _backend(tmp_path)
    backend.upsert_host(host_id="h", hostname="x", os="o", accelerator=None)
    backend.upsert_models(
        [{"model_key": "st:e", "kind": "embedder", "provider": "st", "model_id": "e"}]
    )
    for rate in (1.0, 2.0):
        backend.insert_model_benchmark(
            host_id="h",
            model_key="st:e",
            source="embed-run",
            transport="local",
            device="cpu",
            batch_size=None,
            sample_chunks=100,
            chunks_per_s=rate,
        )
    rows = backend._execute("SELECT chunks_per_s FROM model_benchmarks ORDER BY id", ())
    assert [r["chunks_per_s"] for r in rows] == pytest.approx([1.0, 2.0])
