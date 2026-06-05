"""Unit tests for ``corpus-forge bench embed`` (rfc-fleet-1 item 4).

Coverage targets:

* sampling preference — real pending chunks first, synthetic fallback
  when the lane is fully embedded;
* the persisted-vs-not rule — real-pending vectors ARE written,
  synthetic vectors are NEVER written;
* one ``model_benchmarks`` row per benched embedder, ``source="bench"``;
* transport / device tagging (local vs api → remote);
* per-embedder failure isolation + ``all_failed`` exit semantics;
* ``--json`` payload shape + Rich table render smoke.

The embedder + backend are doubled so the tests run without a real
model or DB.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from corpus_forge.admin import bench

# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


class _StubEmbedderConfig:
    def __init__(
        self,
        name: str,
        *,
        provider: str = "sentence_transformers",
        model_id: str = "test-model",
        base_url: str | None = None,
        active: bool = True,
        batch_size: int = 32,
    ) -> None:
        self.name = name
        self.provider = provider
        self.model_id = model_id
        self.base_url = base_url
        self.active = active
        self.batch_size = batch_size


class _StubConfig:
    def __init__(self, embedders: list[_StubEmbedderConfig], host_id: str = "host-1") -> None:
        self.embedders = embedders
        self._host_id = host_id

    def host_id(self) -> str:
        return self._host_id


class _StubEmbedder:
    """Minimal embedder double: catchall, returns deterministic vectors."""

    def __init__(self, name: str, *, dim: int = 4, fail_encode: bool = False) -> None:
        self.name = name
        self.model_id = "test-model"
        self.dimension = dim
        self.extensions: list[str] = []
        self._dim = dim
        self._fail_encode = fail_encode
        self.warmed = False

    def warmup(self) -> None:
        self.warmed = True

    def encode(self, texts: Any) -> Any:
        if self._fail_encode:
            raise RuntimeError("boom")
        return np.array([[0.1] * self._dim for _ in texts], dtype="float32")


class _StubBackend:
    """Backend double recording benchmark rows + write_embeddings calls."""

    def __init__(self, pending: list[tuple[int, str, str]] | None = None) -> None:
        self._pending = pending or []
        self.benchmark_rows: list[dict] = []
        self.written: list[tuple[int, list]] = []
        self.host_calls: list[dict] = []
        self.model_calls: list[list[dict]] = []

    # registry / heartbeat surface
    def upsert_host(self, **kwargs: Any) -> None:
        self.host_calls.append(kwargs)

    def upsert_models(self, rows: list[dict]) -> None:
        self.model_calls.append(rows)

    def register_embedder(self, embedder: Any) -> int:
        return 1

    def chunks_missing_embedding(
        self, embedder_id: int, limit: int = 1024, **kwargs: Any
    ) -> list[tuple[int, str, str]]:
        return list(self._pending[:limit])

    def write_embeddings(self, embedder_id: int, pairs: list) -> None:
        self.written.append((embedder_id, list(pairs)))

    def insert_model_benchmark(self, **kwargs: Any) -> None:
        self.benchmark_rows.append(kwargs)


def _patch_registry(monkeypatch: pytest.MonkeyPatch, embedder: _StubEmbedder) -> None:
    """Make ``register_from_config`` yield our stub embedder + skip heartbeat."""

    monkeypatch.setattr(
        "corpus_forge.embedders.registry.register_from_config",
        lambda registry, cfg: embedder,
    )
    # Keep the heartbeat a no-op so the bench focuses on its own rows.
    monkeypatch.setattr("corpus_forge.telemetry_registry.heartbeat", lambda backend, config: None)
    # Pin the device probe so device tagging is deterministic.
    monkeypatch.setattr(
        bench, "resolve_device", lambda transport: "cpu" if transport == "local" else "remote"
    )


# ---------------------------------------------------------------------------
# synthetic_sample
# ---------------------------------------------------------------------------


def test_synthetic_sample_is_deterministic() -> None:
    a = bench.synthetic_sample(10)
    b = bench.synthetic_sample(10)
    assert a == b
    assert len(a) == 10
    # Varied length — not all the same word count.
    lengths = {len(t.split()) for t in a}
    assert len(lengths) > 1


def test_synthetic_sample_zero_and_negative() -> None:
    assert bench.synthetic_sample(0) == []
    assert bench.synthetic_sample(-5) == []


# ---------------------------------------------------------------------------
# transport / device tagging
# ---------------------------------------------------------------------------


def test_resolve_transport_local_vs_api() -> None:
    assert bench.resolve_transport(_StubEmbedderConfig("a")) == "local"
    assert (
        bench.resolve_transport(_StubEmbedderConfig("b", base_url="https://api.example/v1"))
        == "api"
    )


def test_resolve_device_api_is_remote() -> None:
    assert bench.resolve_device("api") == "remote"


def test_resolve_device_local_uses_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    from corpus_forge.acceleration import Accelerator, AcceleratorInfo

    monkeypatch.setattr(
        "corpus_forge.acceleration.detect_accelerator",
        lambda: AcceleratorInfo(kind=Accelerator.CUDA, device_name="RTX", vram_mb=1),
    )
    assert bench.resolve_device("local") == "cuda"


def test_resolve_device_probe_failure_degrades_to_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom() -> Any:
        raise RuntimeError("no probe")

    monkeypatch.setattr("corpus_forge.acceleration.detect_accelerator", _boom)
    assert bench.resolve_device("local") == "cpu"


# ---------------------------------------------------------------------------
# count_tokens
# ---------------------------------------------------------------------------


def test_count_tokens_none_when_no_tokenizer() -> None:
    assert bench.count_tokens(_StubEmbedder("x"), ["hello world"]) is None


def test_count_tokens_sentence_transformers_shape() -> None:
    class _Tok:
        def encode(self, text: str) -> list[int]:
            return list(range(len(text.split())))

    class _Model:
        tokenizer = _Tok()

    class _Emb:
        _model = _Model()

    assert bench.count_tokens(_Emb(), ["a b c", "d e"]) == 5


def test_count_tokens_llama_cpp_shape() -> None:
    class _Emb:
        def tokenize(self, data: bytes) -> list[int]:
            return list(range(len(data.split())))

    assert bench.count_tokens(_Emb(), ["a b", "c d e"]) == 5


def test_count_tokens_swallows_failure() -> None:
    class _Tok:
        def encode(self, text: str) -> list[int]:
            raise ValueError("bad")

    class _Model:
        tokenizer = _Tok()

    class _Emb:
        _model = _Model()

    assert bench.count_tokens(_Emb(), ["x"]) is None


# ---------------------------------------------------------------------------
# _percentile
# ---------------------------------------------------------------------------


def test_percentile_basic() -> None:
    assert bench._percentile([5.0], 95.0) == 5.0
    vals = [1.0, 2.0, 3.0, 4.0]
    assert bench._percentile(vals, 50.0) == pytest.approx(2.5)
    assert bench._percentile(vals, 0.0) == 1.0
    assert bench._percentile(vals, 100.0) == 4.0


def test_percentile_empty_raises() -> None:
    with pytest.raises(ValueError):
        bench._percentile([], 50.0)


# ---------------------------------------------------------------------------
# sampling preference + persisted-vs-not
# ---------------------------------------------------------------------------


def test_bench_one_real_pending_persists(monkeypatch: pytest.MonkeyPatch) -> None:
    emb = _StubEmbedder("local-emb")
    _patch_registry(monkeypatch, emb)
    backend = _StubBackend(pending=[(1, "t1", ""), (2, "t2", ""), (3, "t3", "")])
    cfg = _StubEmbedderConfig("local-emb")

    result = bench.bench_one(backend, cfg, host_id="host-1", sample=64)

    assert result.error is None
    assert result.source_kind == "real-pending"
    assert result.persisted is True
    # Vectors written back for the 3 sampled chunks.
    assert len(backend.written) == 1
    assert len(backend.written[0][1]) == 3
    # One benchmark row, source=bench.
    assert len(backend.benchmark_rows) == 1
    assert backend.benchmark_rows[0]["source"] == "bench"
    assert backend.benchmark_rows[0]["sample_chunks"] == 3


def test_bench_one_synthetic_never_persists(monkeypatch: pytest.MonkeyPatch) -> None:
    emb = _StubEmbedder("local-emb")
    _patch_registry(monkeypatch, emb)
    backend = _StubBackend(pending=[])  # no backlog → synthetic fallback
    cfg = _StubEmbedderConfig("local-emb")

    result = bench.bench_one(backend, cfg, host_id="host-1", sample=8)

    assert result.error is None
    assert result.source_kind == "synthetic"
    assert result.persisted is False
    # CRITICAL: no synthetic vectors written.
    assert backend.written == []
    # Benchmark row still recorded.
    assert len(backend.benchmark_rows) == 1
    assert backend.benchmark_rows[0]["sample_chunks"] == 8


def test_bench_one_tags_api_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    emb = _StubEmbedder("api-emb")
    _patch_registry(monkeypatch, emb)
    backend = _StubBackend(pending=[])
    cfg = _StubEmbedderConfig("api-emb", provider="openai", base_url="https://api.x/v1")

    result = bench.bench_one(backend, cfg, host_id="host-1", sample=4)

    assert result.transport == "api"
    assert result.device == "remote"
    # API transport encodes per-text → latency distribution present.
    assert result.latency_p50_ms is not None
    assert result.latency_p95_ms is not None
    assert backend.benchmark_rows[0]["transport"] == "api"


def test_bench_one_local_has_no_latency(monkeypatch: pytest.MonkeyPatch) -> None:
    emb = _StubEmbedder("local-emb")
    _patch_registry(monkeypatch, emb)
    backend = _StubBackend(pending=[])
    cfg = _StubEmbedderConfig("local-emb")

    result = bench.bench_one(backend, cfg, host_id="host-1", sample=4)
    assert result.latency_p50_ms is None
    assert result.latency_p95_ms is None


def test_bench_one_load_failure_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(registry: Any, cfg: Any) -> Any:
        raise RuntimeError("model not found")

    monkeypatch.setattr("corpus_forge.embedders.registry.register_from_config", _raise)
    monkeypatch.setattr(bench, "resolve_device", lambda t: "cpu")
    backend = _StubBackend()
    cfg = _StubEmbedderConfig("broken")

    result = bench.bench_one(backend, cfg, host_id="host-1", sample=4)
    assert result.error is not None
    assert "load failed" in result.error
    assert backend.benchmark_rows == []


def test_bench_one_encode_failure_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    emb = _StubEmbedder("local-emb", fail_encode=True)
    _patch_registry(monkeypatch, emb)
    backend = _StubBackend(pending=[])
    cfg = _StubEmbedderConfig("local-emb")

    result = bench.bench_one(backend, cfg, host_id="host-1", sample=4)
    assert result.error is not None
    assert "encode failed" in result.error


def test_bench_one_persist_failure_keeps_benchmark(monkeypatch: pytest.MonkeyPatch) -> None:
    emb = _StubEmbedder("local-emb")
    _patch_registry(monkeypatch, emb)

    class _PersistFails(_StubBackend):
        def write_embeddings(self, embedder_id: int, pairs: list) -> None:
            raise RuntimeError("disk full")

    backend = _PersistFails(pending=[(1, "t1", "")])
    cfg = _StubEmbedderConfig("local-emb")

    result = bench.bench_one(backend, cfg, host_id="host-1", sample=4)
    # Persist failed → persisted False, but the benchmark numbers survive.
    assert result.error is None
    assert result.persisted is False
    assert result.chunks_per_s is not None
    assert len(backend.benchmark_rows) == 1


def test_bench_one_insert_failure_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    emb = _StubEmbedder("local-emb")
    _patch_registry(monkeypatch, emb)

    class _InsertFails(_StubBackend):
        def insert_model_benchmark(self, **kwargs: Any) -> None:
            raise RuntimeError("no table")

    backend = _InsertFails(pending=[])
    cfg = _StubEmbedderConfig("local-emb")

    # A failed benchmark insert must not break the bench result.
    result = bench.bench_one(backend, cfg, host_id="host-1", sample=4)
    assert result.error is None


# ---------------------------------------------------------------------------
# orchestration: bench_embedders + target selection
# ---------------------------------------------------------------------------


def test_bench_embedders_all_and_sort(monkeypatch: pytest.MonkeyPatch) -> None:
    # Two embedders, second one is slower → first should sort first.
    fast = _StubEmbedder("fast")
    slow = _StubEmbedder("slow")
    embedders = {"fast": fast, "slow": slow}

    def _reg(registry: Any, cfg: Any) -> Any:
        return embedders[cfg.name]

    monkeypatch.setattr("corpus_forge.embedders.registry.register_from_config", _reg)
    monkeypatch.setattr("corpus_forge.telemetry_registry.heartbeat", lambda b, c: None)
    monkeypatch.setattr(bench, "resolve_device", lambda t: "cpu")

    # Make 'slow' sleep so its chunks_per_s is lower.
    real_encode = slow.encode

    def _slow_encode(texts: Any) -> Any:
        import time

        time.sleep(0.01)
        return real_encode(texts)

    slow.encode = _slow_encode  # type: ignore[method-assign]

    backend = _StubBackend(pending=[])
    cfg = _StubConfig([_StubEmbedderConfig("fast"), _StubEmbedderConfig("slow")])

    report = bench.bench_embedders(backend, cfg, all_=True, sample=4)
    assert [r.embedder_name for r in report.results] == ["fast", "slow"]
    assert not report.all_failed
    assert len(backend.benchmark_rows) == 2


def test_bench_one_sample_query_failure_falls_back_to_synthetic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    emb = _StubEmbedder("local-emb")
    _patch_registry(monkeypatch, emb)

    class _QueryFails(_StubBackend):
        def chunks_missing_embedding(self, *a: Any, **k: Any) -> Any:
            raise RuntimeError("db gone")

    backend = _QueryFails()
    cfg = _StubEmbedderConfig("local-emb")
    result = bench.bench_one(backend, cfg, host_id="host-1", sample=4)
    # Query failure → synthetic fallback (never persisted).
    assert result.source_kind == "synthetic"
    assert result.persisted is False
    assert backend.written == []


def test_bench_one_two_tuple_rows_treated_as_catchall(monkeypatch: pytest.MonkeyPatch) -> None:
    emb = _StubEmbedder("local-emb")
    _patch_registry(monkeypatch, emb)

    class _TwoTuple(_StubBackend):
        def chunks_missing_embedding(self, *a: Any, **k: Any) -> Any:
            return [(1, "t1"), (2, "t2")]  # legacy 2-tuple stub

    backend = _TwoTuple()
    cfg = _StubEmbedderConfig("local-emb")
    result = bench.bench_one(backend, cfg, host_id="host-1", sample=4)
    assert result.source_kind == "real-pending"
    assert result.sample_chunks == 2


def test_bench_one_no_chunks_empty_synthetic(monkeypatch: pytest.MonkeyPatch) -> None:
    emb = _StubEmbedder("local-emb")
    _patch_registry(monkeypatch, emb)
    backend = _StubBackend(pending=[])
    cfg = _StubEmbedderConfig("local-emb")
    # sample=0 + empty backlog → synthetic_sample(0) == [] → error row.
    result = bench.bench_one(backend, cfg, host_id="host-1", sample=0)
    assert result.error is not None
    assert "no chunks" in result.error


def test_bench_embedders_named_unknown_raises() -> None:
    cfg = _StubConfig([_StubEmbedderConfig("known")])
    backend = _StubBackend()
    # heartbeat happens first; patch via the real one tolerating our stub.
    with pytest.raises(ValueError, match="not found"):
        bench.bench_embedders(backend, cfg, embedders=["missing"], sample=4)


def test_select_targets_default_active() -> None:
    cfg = _StubConfig(
        [
            _StubEmbedderConfig("a", active=True),
            _StubEmbedderConfig("b", active=False),
        ]
    )
    targets = bench._select_targets(cfg, None, False)
    assert [t.name for t in targets] == ["a"]


def test_select_targets_default_falls_back_to_all_when_none_active() -> None:
    cfg = _StubConfig([_StubEmbedderConfig("a", active=False)])
    targets = bench._select_targets(cfg, None, False)
    assert [t.name for t in targets] == ["a"]


def test_select_targets_named() -> None:
    cfg = _StubConfig([_StubEmbedderConfig("a"), _StubEmbedderConfig("b")])
    targets = bench._select_targets(cfg, ["b"], False)
    assert [t.name for t in targets] == ["b"]


def test_select_targets_named_unknown_raises() -> None:
    cfg = _StubConfig([_StubEmbedderConfig("a")])
    with pytest.raises(ValueError, match="not found"):
        bench._select_targets(cfg, ["nope"], False)


# ---------------------------------------------------------------------------
# _build_backend
# ---------------------------------------------------------------------------


def test_build_backend_sqlite(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    class _Cfg:
        class backend:
            kind = "sqlite"
            dsn = str(tmp_path / "c.db")
            schema = "corpus"

    backend = bench._build_backend(_Cfg())
    assert backend.__class__.__name__ == "SQLiteBackend"


def test_build_backend_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    created = {}

    class _FakePg:
        def __init__(self, dsn: str, schema: str) -> None:
            created["dsn"] = dsn
            created["schema"] = schema

        def migrate(self) -> None:
            created["migrated"] = True

    monkeypatch.setattr("corpus_forge.backends.postgres.PostgresBackend", _FakePg)

    class _Cfg:
        class backend:
            kind = "postgres"
            dsn = "postgresql://x"
            schema = "corpus"

    backend = bench._build_backend(_Cfg())
    assert isinstance(backend, _FakePg)
    assert created["migrated"] is True


def test_sample_pending_respects_sample_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """More 2-tuple rows than ``sample`` → only ``sample`` pairs kept (break path)."""
    emb = _StubEmbedder("local-emb")

    class _ManyTwoTuple(_StubBackend):
        def chunks_missing_embedding(self, *a: Any, **k: Any) -> Any:
            return [(i, f"t{i}") for i in range(10)]

    backend = _ManyTwoTuple()
    pairs = bench._sample_pending(backend, emb, 1, 3)
    assert len(pairs) == 3


def test_build_backend_unsupported_kind() -> None:
    class _Cfg:
        class backend:
            kind = "duckdb"
            dsn = "x"
            schema = "corpus"

    with pytest.raises(ValueError, match="Unsupported backend kind"):
        bench._build_backend(_Cfg())


def test_bench_embedders_all_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(registry: Any, cfg: Any) -> Any:
        raise RuntimeError("nope")

    monkeypatch.setattr("corpus_forge.embedders.registry.register_from_config", _raise)
    monkeypatch.setattr("corpus_forge.telemetry_registry.heartbeat", lambda b, c: None)
    monkeypatch.setattr(bench, "resolve_device", lambda t: "cpu")
    backend = _StubBackend()
    cfg = _StubConfig([_StubEmbedderConfig("a"), _StubEmbedderConfig("b")])

    report = bench.bench_embedders(backend, cfg, all_=True, sample=4)
    assert report.all_failed is True


# ---------------------------------------------------------------------------
# rendering + json shape
# ---------------------------------------------------------------------------


def test_render_table_smoke(monkeypatch: pytest.MonkeyPatch) -> None:
    emb = _StubEmbedder("local-emb")
    _patch_registry(monkeypatch, emb)
    backend = _StubBackend(pending=[])
    cfg = _StubConfig([_StubEmbedderConfig("local-emb")])
    report = bench.bench_embedders(backend, cfg, all_=True, sample=4)

    table = bench.render_table(report)
    assert table.row_count == 1


def test_render_table_error_row(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "corpus_forge.embedders.registry.register_from_config",
        lambda r, c: (_ for _ in ()).throw(RuntimeError("x")),
    )
    monkeypatch.setattr("corpus_forge.telemetry_registry.heartbeat", lambda b, c: None)
    monkeypatch.setattr(bench, "resolve_device", lambda t: "cpu")
    backend = _StubBackend()
    cfg = _StubConfig([_StubEmbedderConfig("broken")])
    report = bench.bench_embedders(backend, cfg, all_=True, sample=4)
    table = bench.render_table(report)
    assert table.row_count == 1


def test_report_to_dict_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    emb = _StubEmbedder("local-emb")
    _patch_registry(monkeypatch, emb)
    backend = _StubBackend(pending=[(1, "t1", "")])
    cfg = _StubConfig([_StubEmbedderConfig("local-emb")])
    report = bench.bench_embedders(backend, cfg, all_=True, sample=4)

    payload = bench.report_to_dict(report)
    assert payload["host_id"] == "host-1"
    assert payload["all_failed"] is False
    assert len(payload["results"]) == 1
    row = payload["results"][0]
    for key in (
        "embedder",
        "model_key",
        "transport",
        "device",
        "sample_chunks",
        "chunks_per_s",
        "tokens_per_s",
        "latency_p50_ms",
        "latency_p95_ms",
        "source_kind",
        "persisted",
        "error",
    ):
        assert key in row
    assert row["source_kind"] == "real-pending"
    assert row["persisted"] is True
